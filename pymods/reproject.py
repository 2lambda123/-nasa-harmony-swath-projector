"""
 Data Services Reprojection service for Harmony
"""
from typing import Dict, Tuple
import argparse
import functools
import json
import logging
import os
import re
import shutil
from tempfile import mkdtemp

from pyproj import Proj
from pyresample import geometry
from rasterio.transform import Affine
import rasterio
import xarray
from harmony.message import Message

from pymods import nc_merge
from pymods.nc_info import NCInfo
from pymods.interpolation import resample_all_variables
from pymods.swotrepr_geometry import (get_extents_from_perimeter,
                                      get_projected_resolution)

RADIUS_EARTH_METRES = 6_378_137  # http://nssdc.gsfc.nasa.gov/planetary/factsheet/earthfact.html
CRS_DEFAULT = '+proj=longlat +ellps=WGS84'
INTERPOLATION_DEFAULT = 'ewa-nn'


def reproject(msg: Message, filename: str, temp_dir: str, logger: logging.Logger) -> str:
    """ Derive reprojection parameters from the input Harmony message. Then
        extract listing of science variables and coordinate variables from the
        source granule. Then reproject all science variables. Finally merge all
        individual output bands back into a single netCDF-4 file.

    """
    # Set up source and destination files
    param_list = get_params_from_msg(msg, filename, logger)

    root_ext = os.path.splitext(os.path.basename(param_list.get('input_file')))
    output_file = temp_dir + os.sep + root_ext[0] + '_repr' + root_ext[1]

    logger.info(f'Reprojecting file {param_list.get("input_file")} as {output_file}')
    logger.info(f'Selected CRS: {param_list.get("crs")}\t'
                f'Interpolation: {param_list.get("interpolation")}')

    try:
        info = NCInfo(param_list['input_file'])
    except Exception as err:
        logger.error(f'Unable to parse input file variables: {str(err)}')
        raise Exception('Unable to parse input file varialbes')

    science_variables = info.get_science_variables()

    if len(science_variables) == 0:
        raise Exception('No science variables found in input file')

    logger.info(f'Input file has {len(science_variables)} science variables')

    # Loop through each dataset and reproject
    logger.debug('Using pyresample for reprojection.')
    outputs = resample_all_variables(param_list, science_variables, temp_dir,
                                     logger)

    if not outputs:
        raise Exception("No subdatasets could be reprojected")

    # Now merge outputs (unless we only have one)
    metadata_variables = info.get_metadata_variables()
    nc_merge.create_output(param_list.get('input_file'), output_file, temp_dir,
                           science_variables, metadata_variables, logger)

    # Return the output file back to Harmony
    return output_file


def get_params_from_msg(message, input_file, logger):
    """ A helper function to parse the input Harmony message and extract
        required information. If the message is missing parameters, then
        default values will be used.

    """
    # TODO: Full refactor, including breaking out separate functions, and
    #       removing use of `locals()` (also, ensuring all members of the
    #       output dictionary are actually used, if not removing them).
    crs = rgetattr(message, 'format.crs') or CRS_DEFAULT
    interpolation = rgetattr(message, 'format.interpolation',
                             INTERPOLATION_DEFAULT)  # near, bilinear, ewa

    if interpolation in [None, '', 'None']:
        interpolation = INTERPOLATION_DEFAULT

    x_extent = rgetattr(message, 'format.scaleExtent.x', None)
    y_extent = rgetattr(message, 'format.scaleExtent.y', None)
    width = rgetattr(message, 'format.width', None)
    height = rgetattr(message, 'format.height', None)
    xres = rgetattr(message, 'format.scaleSize.x', None)
    yres = rgetattr(message, 'format.scaleSize.y', None)

    # Mark properties we use as having been processed
    message.format.process(
        'crs',
        'interpolation',
        'scaleExtent',
        'width',
        'height',
        'scaleSize')

    # ERROR 5: -tr and -ts options cannot be used at the same time.
    if (
            (xres is not None or yres is not None) and
            (height is not None or width is not None)
    ):
        raise Exception("'scaleSize', 'width' or/and 'height' cannot "
                        "be used at the same time in the message.")

    if input_file is None:
        raise Exception('Invalid local_filename attribute for granule.')
    elif not os.path.isfile(input_file):
        raise Exception("Input file does not exist")

    # refactor to get groups and datasets together (first?)
    try:
        latlon_group, data_group = get_group(input_file)
        file_data = get_input_file_data(input_file, latlon_group)
        latitudes = file_data.get('latitudes')
        longitudes = file_data.get('longitudes')
        lon_res = file_data.get('lon_res')
        lat_res = file_data.get('lat_res')
    except Exception as err:
        logger.error(f'Unable to determine input file format: {str(err)}')
        raise Exception('Cannot determine input file format')

    projection = Proj(crs)

    # Verify message and assign values

    if not x_extent and y_extent:
        raise Exception("Missing x extent")
    if x_extent and not y_extent:
        raise Exception("Missing y extent")
    if width and not height:
        raise Exception("Missing cell height")
    if height and not width:
        raise Exception("Missing cell width")

    if x_extent:
        x_min = rgetattr(x_extent, 'min', None)
        x_max = rgetattr(x_extent, 'max', None)
    if y_extent:
        y_min = rgetattr(y_extent, 'min', None)
        y_max = rgetattr(y_extent, 'max', None)

    if x_extent is None and y_extent is None:
        # If extents aren't specified, they should be the input ranges
        x_min, x_max, y_min, y_max = get_extents_from_perimeter(projection,
                                                                longitudes,
                                                                latitudes)
        logger.info(f'Calculated x extent: x_min: {x_min}, x_max: {x_max}')
        logger.info(f'Calculated y extent: y_min: {y_min}, y_max: {y_max}')
    else:
        logger.info(f'Message x extent: x_min: {x_min}, x_max: {x_max}')
        logger.info(f'Message y extent: y_min: {y_min}, y_max: {y_max}')

    if (
            (xres is None or yres is None) and
            (width is not None and height is not None)
    ):
        xres = (x_max - x_min) / width
        # Note: This hard-codes a negative y-resolution
        yres = (y_min - y_max) / height
        logger.info(f'Calculated x resolution from width: {xres}')
        logger.info(f'Calculated y resolution from height: {yres}')
    elif (xres is None or yres is None):
        xres = get_projected_resolution(projection, longitudes, latitudes)
        # TODO: Determine sign of y resolution from projected y data.
        yres = -1.0 * xres
        logger.info(f'Calculated projected resolutions: ({xres}, {yres})')
    else:
        logger.info(f'Resolutions from message: ({xres}, {yres})')

    if not width and not height:
        # TODO: Handle width if Geographic coordinates and crossing dateline
        width = abs(round((x_min - x_max) / xres))
        height = abs(round((y_min - y_max) / yres))
        logger.info(f'Calculated width: {width}')
        logger.info(f'Calculated height: {height}')

    # GDAL Standard geo-transform tuple
    geotransform = (x_min, xres, 0.0, y_max, 0.0, yres)
    grid_transform = Affine.from_gdal(*geotransform)

    return locals()


def get_group(file_name: str) -> Tuple[str, str]:
    dataset = rasterio.open(file_name)
    latlon_group = None
    data_group = None

    for subdataset in dataset.subdatasets:
        dataset_path = re.sub(r'.*\.nc:(.*)', r'\1', subdataset)
        dataset_path_arr = dataset_path.split('/')
        prefix = dataset_path_arr[0:-1]  # if dataset_path.len > 1 else ""

        if 'lat' in dataset_path or 'lon' in dataset_path:
            latlon_group = "/".join(prefix)
        else:
            data_group = "/".join(prefix)

        if latlon_group is not None and data_group is not None:
            # early exit from loop
            break

    return latlon_group, data_group


def get_input_file_data(file_name: str, group: str) -> Dict:
    """Get the input dataset (sea surface temperature) and coordinate
    information. Using the coordinate information derive a swath
    definition.

    :rtype: dictionary

    """
    with xarray.open_dataset(file_name, decode_cf=True, group=group) as dataset:
        try:
            latitudes = dataset.coords.variables('lat')
            longitudes = dataset.coords.variables('lon')
        except:
            variables = dataset.variables
            for variable in dataset.variables:
                if 'lat' in variable:
                    latitudes = variables[variable]
                elif 'lon' in variable:
                    longitudes = variables[variable]

        metadata = dataset.attrs
        lat_res = dataset.attrs.get('geospatial_lat_resolution')
        lon_res = dataset.attrs.get('geospatial_lon_resolution')

    swath_definition = geometry.SwathDefinition(lons=longitudes.values,
                                                lats=latitudes.values)
    return {'latitudes': latitudes,
            'longitudes': longitudes,
            'metadata': metadata,
            'swath_definition': swath_definition,
            'lat_res': lat_res,
            'lon_res': lon_res}


def rgetattr(obj, attr: str, *args):
    """ Recursive get attribute
        Returns attribute from an attribute hierarchy, e.g. a.b.c, if it exists
    """

    # functools.reduce will apply _getattr with previous result (obj)
    #   and item from sequence (attr)
    def _getattr(obj, attr):
        return getattr(obj, attr, *args)

    # First call takes first two items, thus need [obj] as first item in sequence
    return functools.reduce(_getattr, [obj] + attr.split('.'))


def to_object(item):
    """ Recursively converts item into object with attributes
        E.g., a dictionary becomes an object with "." access attributes
        Useful for e.g., converting json objects into "full" objects
    """
    if isinstance(item, dict):
        # return a new object with the dictionary items as attributes
        return type('obj', (), {k: to_object(v) for k, v in item.items()})
    if isinstance(item, list):
        def yield_convert(item):
            """ Acts as a generator (iterator) returning one item for successive calls """
            for index, value in enumerate(item):
                yield to_object(value)
        # list will iterate on generator (yield_convert) and combine the results into a list
        return list(yield_convert(item))
    else:
        return item


# Main program start for testing
#
if __name__ == "__main__":
    PARSER = argparse.ArgumentParser(
        prog='Reproject',
        description='Run the Data Services Reprojection Tool'
    )
    PARSER.add_argument('--message',
                        help='The input data for the action provided by Harmony')

    ARGS = PARSER.parse_args()
    # Note it is hard to get properly quoted json string through shell invocation,
    # It is easier if single and double quoting is inverted
    quoted_msg = re.sub("'", '"', ARGS.message)
    msg_dictionary = json.loads(quoted_msg)
    msg = to_object(msg_dictionary)

    logger = logging.getLogger("SwotRepr")
    syslog = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s"
    )
    #       "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] [%(user)s] %(message)s")
    syslog.setFormatter(formatter)
    logger.addHandler(syslog)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    workdir = mkdtemp()
    try:
        reproject(msg, msg.sources[0].granules[0].url, workdir, logger)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)