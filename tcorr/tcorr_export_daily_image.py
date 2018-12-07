#--------------------------------
# Name:         tcorr_export_daily_image.py
# Purpose:      Compute/Export daily Tcorr images
#--------------------------------

import argparse
from builtins import input
import datetime
import logging
import math
import os
import pprint
import sys

import ee

import openet.ssebop as ssebop
import utils


def main(ini_path=None, overwrite_flag=False, delay=0, key=None):
    """Compute daily Tcorr images

    Parameters
    ----------
    ini_path : str
        Input file path.
    overwrite_flag : bool, optional
        If True, overwrite existing files (the default is False).
    delay : float, optional
        Delay time between each export task (the default is 0).
    key : str, optional
        File path to an Earth Engine json key file (the default is None).

    """
    logging.info('\nCompute daily Tcorr images')

    ini = utils.read_ini(ini_path)

    if (ini['SSEBOP']['tmax_source'].upper() == 'CIMIS' and
            ini['INPUTS']['end_date'] < '2003-10-01'):
        logging.error(
            '\nCIMIS is not currently available before 2003-10-01, exiting\n')
        sys.exit()
    elif (ini['SSEBOP']['tmax_source'].upper() == 'DAYMET' and
            ini['INPUTS']['end_date'] > '2017-12-31'):
        logging.warning(
            '\nDAYMET is not currently available past 2017-12-31, '
            'using median Tmax values\n')
        # sys.exit()
    # elif (ini['SSEBOP']['tmax_source'].upper() == 'TOPOWX' and
    #         ini['INPUTS']['end_date'] > '2017-12-31'):
    #     logging.warning(
    #         '\nDAYMET is not currently available past 2017-12-31, '
    #         'using median Tmax values\n')
    #     # sys.exit()

    logging.info('\nInitializing Earth Engine')
    if key:
        logging.info('  Using service account key file: {}'.format(key))
        # The "EE_ACCOUNT" parameter is not used if the key file is valid
        ee.Initialize(ee.ServiceAccountCredentials('deadbeef', key_file=key))
    else:
        ee.Initialize()

    # Output Tcorr daily image collection
    tcorr_daily_coll_id = '{}/{}_daily'.format(
        ini['EXPORT']['export_path'], ini['SSEBOP']['tmax_source'].lower())

    # Get a Tmax image to set the Tcorr values to
    logging.debug('\nTmax properties')
    tmax_name = ini['SSEBOP']['tmax_source']
    tmax_source = tmax_name.split('_', 1)[0]
    tmax_version = tmax_name.split('_', 1)[1]
    tmax_coll_id = 'projects/usgs-ssebop/tmax/{}'.format(tmax_name.lower())
    tmax_coll = ee.ImageCollection(tmax_coll_id)
    tmax_img = ee.Image(tmax_coll.first())
    logging.debug('  Collection: {}'.format(tmax_coll_id))
    logging.debug('  Source: {}'.format(tmax_source))
    logging.debug('  Version: {}'.format(tmax_version))

    logging.debug('\nExport properties')
    export_geo = ee.Image(tmax_img).projection().getInfo()['transform']
    export_crs = ee.Image(tmax_img).projection().getInfo()['crs']
    export_shape = ee.Image(tmax_img).getInfo()['bands'][0]['dimensions']
    export_extent = [
        export_geo[2], export_geo[5] + export_shape[1] * export_geo[4],
        export_geo[2] + export_shape[0] * export_geo[0], export_geo[5]]
    logging.debug('  CRS: {}'.format(export_crs))
    logging.debug('  Extent: {}'.format(export_extent))
    logging.debug('  Geo: {}'.format(export_geo))
    logging.debug('  Shape: {}'.format(export_shape))

    # # Limit export to a user defined study area or geometry?
    # export_geom = ee.Geometry.Rectangle(
    #     [-125, 24, -65, 50], proj='EPSG:4326', geodesic=False)  # CONUS
    # export_geom = ee.Geometry.Rectangle(
    #     [-124, 35, -119, 42], proj='EPSG:4326', geodesic=False)  # California

    # If cell_size parameter is set in the INI,
    # adjust the output cellsize and recompute the transform and shape
    try:
        export_cs = float(ini['EXPORT']['cell_size'])
        export_shape = [
            int(math.ceil(abs((export_shape[0] * export_geo[0]) / export_cs))),
            int(math.ceil(abs((export_shape[1] * export_geo[4]) / export_cs)))]
        export_geo = [export_cs, 0.0, export_geo[2], 0.0, -export_cs, export_geo[5]]
        logging.debug('  Custom export cell size: {}'.format(export_cs))
        logging.debug('  Geo: {}'.format(export_geo))
        logging.debug('  Shape: {}'.format(export_shape))
    except KeyError:
        pass

    # Get current asset list
    if ini['EXPORT']['export_dest'].upper() == 'ASSET':
        logging.debug('\nGetting asset list')
        # DEADBEEF - daily is hardcoded in the asset_id for now
        asset_list = utils.get_ee_assets(tcorr_daily_coll_id)
    else:
        raise ValueError('invalid export destination: {}'.format(
            ini['EXPORT']['export_dest']))

    # Get current running tasks
    tasks = utils.get_ee_tasks()
    if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
        logging.debug('  Tasks: {}\n'.format(len(tasks)))
        input('ENTER')

    iter_start_dt = datetime.datetime.strptime(
        ini['INPUTS']['start_date'], '%Y-%m-%d')
    iter_end_dt = datetime.datetime.strptime(
        ini['INPUTS']['end_date'], '%Y-%m-%d')

    # Iterate over date ranges
    for export_dt in utils.date_range(iter_start_dt, iter_end_dt):
        export_date = export_dt.strftime('%Y-%m-%d')
        logging.info('Date: {}'.format(export_date))

        if export_date >= datetime.datetime.today().strftime('%Y-%m-%d'):
            logging.info('  Unsupported date, skipping')
            continue
        elif export_date < '1984-03-23':
            logging.info('  No Landsat 5+ images before 1984-03-16, skipping')
            continue

        export_id = ini['EXPORT']['export_id_fmt'] \
            .format(
                product=ini['SSEBOP']['tmax_source'].lower(),
                date=export_dt.strftime('%Y%m%d'),
                export=ini['EXPORT']['export_dest'].lower())
        logging.debug('  Export ID: {}'.format(export_id))

        if ini['EXPORT']['export_dest'] == 'ASSET':
            # DEADBEEF - daily is hardcoded in the asset_id for now
            asset_id = '{}/{}'.format(
                tcorr_daily_coll_id, export_dt.strftime('%Y%m%d'))
            logging.debug('  Asset ID: {}'.format(asset_id))

        if overwrite_flag:
            if export_id in tasks.keys():
                logging.debug('  Task already submitted, cancelling')
                ee.data.cancelTask(tasks[export_id])
            # This is intentionally not an "elif" so that a task can be
            # cancelled and an existing image/file/asset can be removed
            if (ini['EXPORT']['export_dest'].upper() == 'ASSET' and
                    asset_id in asset_list):
                logging.debug('  Asset already exists, removing')
                ee.data.deleteAsset(asset_id)
        else:
            if export_id in tasks.keys():
                logging.debug('  Task already submitted, exiting')
                continue
            elif (ini['EXPORT']['export_dest'].upper() == 'ASSET' and
                    asset_id in asset_list):
                logging.debug('  Asset already exists, skipping')
                continue

        # Build and merge the Landsat collections
        #     .filterBounds(export_geom) \
        l8_coll = ee.ImageCollection('LANDSAT/LC08/C01/T1_RT_TOA')\
            .filterDate(export_dt, export_dt + datetime.timedelta(days=1))\
            .filterMetadata('CLOUD_COVER_LAND', 'less_than',
                            float(ini['INPUTS']['cloud_cover']))\
            .filterMetadata('DATA_TYPE', 'equals', 'L1TP')\
            .filter(ee.Filter.gt('system:time_start',
                                 ee.Date('2013-03-24').millis()))
        l7_coll = ee.ImageCollection('LANDSAT/LE07/C01/T1_RT_TOA')\
            .filterDate(export_dt, export_dt + datetime.timedelta(days=1))\
            .filterBounds(tmax_img.geometry())\
            .filterMetadata('CLOUD_COVER_LAND', 'less_than',
                            float(ini['INPUTS']['cloud_cover']))\
            .filterMetadata('DATA_TYPE', 'equals', 'L1TP')
        l5_coll = ee.ImageCollection('LANDSAT/LT05/C01/T1_TOA')\
            .filterDate(export_dt, export_dt + datetime.timedelta(days=1))\
            .filterBounds(tmax_img.geometry())\
            .filterMetadata('CLOUD_COVER_LAND', 'less_than',
                            float(ini['INPUTS']['cloud_cover']))\
            .filterMetadata('DATA_TYPE', 'equals', 'L1TP')\
            .filter(ee.Filter.lt('system:time_start',
                                 ee.Date('2011-12-31').millis()))
        # l4_coll = ee.ImageCollection('LANDSAT/LT04/C01/T1_TOA')\
        #     .filterDate(export_dt, export_dt + datetime.timedelta(days=1))\
        #     .filterBounds(tmax_img.geometry())\
        #     .filterMetadata('CLOUD_COVER_LAND', 'less_than',
        #                     float(ini['INPUTS']['cloud_cover']))\
        #     .filterMetadata('DATA_TYPE', 'equals', 'L1TP')\

        landsat_coll = ee.ImageCollection(
            l8_coll.merge(l7_coll).merge(l5_coll))
        # pprint.pprint(landsat_coll.aggregate_histogram('system:index').getInfo())
        # pprint.pprint(ee.Image(landsat_coll.first()).getInfo())
        # input('ENTER')

        def tcorr_img_func(image):
            t_stats = ssebop.Image.from_landsat_c1_toa(
                ee.Image(image),
                tdiff_threshold=float(ini['SSEBOP']['tdiff_threshold'])).tcorr_stats
            tcorr = ee.Algorithms.If(t_stats.get('tcorr_p5'),
                                     ee.Number(t_stats.get('tcorr_p5')), 0)
            # tcorr = ee.Number(t_stats.get('tcorr_p5'))
            count = ee.Number(t_stats.get('tcorr_count'))

            # Remove the merged collection indices from the system:index
            scene_id = ee.List(
                ee.String(image.get('system:index')).split('_')).slice(-3)
            scene_id = ee.String(scene_id.get(0)).cat('_') \
                .cat(ee.String(scene_id.get(1))).cat('_') \
                .cat(ee.String(scene_id.get(2)))

            # return ee.Image([
            #         tmax_img.select([0], ['tcorr']).multiply(0)\
            #             .add(ee.Image.constant(tcorr)).float(),
            #         tmax_img.select([0], ['count']).multiply(0)
            #             .add(ee.Image.constant(count)).int()]) \
            return tmax_img.select([0], ['tcorr'])\
                .multiply(0).add(ee.Image.constant(tcorr))\
                .clip(image.geometry()) \
                .updateMask(1) \
                .setMulti({
                    'system:time_start': image.get('system:time_start'),
                    'SCENE_ID': scene_id,
                    'WRS2_TILE': scene_id.slice(5, 11),
                    'SPACECRAFT_ID': image.get('SPACECRAFT_ID'),
                    'TCORR': tcorr,
                    'COUNT': count,
            })

        tcorr_img_coll = ee.ImageCollection(landsat_coll.map(tcorr_img_func))\
            .filterMetadata('COUNT', 'not_less_than',
                            float(ini['TCORR']['min_pixel_count']))
        # pprint.pprint(tcorr_img_coll.aggregate_histogram('system:index').getInfo())
        # pprint.pprint(ee.Image(tcorr_img_coll.first()).getInfo())
        # input('ENTER')

        # If there are no Tcorr values, return an empty image
        tcorr_img = ee.Algorithms.If(
            tcorr_img_coll.size().gt(0),
            tcorr_img_coll.mean(),
            tmax_img.multiply(0).updateMask(0))

        # Is there a better way of building these strings?
        wrs2_tile_list = ee.Algorithms.If(
            tcorr_img_coll.size().gt(0),
            ee.String(ee.List(ee.Dictionary(tcorr_img_coll \
                .aggregate_histogram('WRS2_TILE')).keys()).join(',')),
            ee.String(''))
        landsat_list = ee.Algorithms.If(
            tcorr_img_coll.size().gt(0),
            ee.String(ee.List(ee.Dictionary(tcorr_img_coll\
                .aggregate_histogram('SPACECRAFT_ID')).keys()).join(',')),
            ee.String(''))

        # Cast to float and set properties
        tcorr_img = ee.Image(tcorr_img).rename(['tcorr']).float()\
            .setMulti({
                'system:time_start': utils.millis(export_dt),
                'WRS2_TILES': wrs2_tile_list,
                'SSEBOP_VERSION': ssebop.__version__,
                'TMAX_SOURCE': tmax_source.upper(),
                'TMAX_VERSION': tmax_version.upper(),
                'EXPORT_DATE': datetime.datetime.today().strftime('%Y-%m-%d'),
                'DATE': export_dt.strftime('%Y-%m-%d'),
                'YEAR': int(export_dt.year),
                'MONTH': int(export_dt.month),
                'DAY': int(export_dt.day),
                'DOY': int(export_dt.strftime('%j')),
                'LANDSAT': landsat_list
            })
        # pprint.pprint(tcorr_img.getInfo())
        # input('ENTER')

        # Build export tasks
        if ini['EXPORT']['export_dest'] == 'ASSET':
            logging.debug('    Building export task')
            task = ee.batch.Export.image.toAsset(
                image=ee.Image(tcorr_img),
                description=export_id,
                assetId=asset_id,
                crs=export_crs,
                crsTransform='[' + ','.join(list(map(str, export_geo))) + ']',
                dimensions='{0}x{1}'.format(*export_shape),
            )
            logging.debug('    Starting export task')
            utils.ee_task_start(task)

        # Pause before starting next task
        utils.delay_task(delay)
        logging.debug('')


def arg_parse():
    """"""
    parser = argparse.ArgumentParser(
        description='Compute/export daily Tcorr images',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '-i', '--ini', type=utils.arg_valid_file,
        help='Input file', metavar='FILE')
    parser.add_argument(
        '--delay', default=0, type=float,
        help='Delay (in seconds) between each export tasks')
    parser.add_argument(
        '--key', type=utils.arg_valid_file, metavar='FILE',
        help='JSON key file')
    parser.add_argument(
        '-o', '--overwrite', default=False, action='store_true',
        help='Force overwrite of existing files')
    parser.add_argument(
        '-d', '--debug', default=logging.INFO, const=logging.DEBUG,
        help='Debug level logging', action='store_const', dest='loglevel')
    args = parser.parse_args()

    # Prompt user to select an INI file if not set at command line
    if not args.ini:
        args.ini = utils.get_ini_path(os.getcwd())
    return args


if __name__ == "__main__":
    args = arg_parse()

    logging.basicConfig(level=args.loglevel, format='%(message)s')
    logging.info('\n{0}'.format('#' * 80))
    logging.info('{0:<20s} {1}'.format(
        'Run Time Stamp:', datetime.datetime.now().isoformat(' ')))
    logging.info('{0:<20s} {1}'.format('Current Directory:', os.getcwd()))
    logging.info('{0:<20s} {1}'.format(
        'Script:', os.path.basename(sys.argv[0])))

    main(ini_path=args.ini, overwrite_flag=args.overwrite, delay=args.delay,
         key=args.key)