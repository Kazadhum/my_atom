#!/usr/bin/env python3

"""
Stereo calibration from opencv
"""

# -------------------------------------------------------------------------------
# --- IMPORTS
# -------------------------------------------------------------------------------

import numpy as np
import cv2
import argparse
import json
import os
import tf

from colorama import Style, Fore
from collections import OrderedDict
from atom_evaluation.utilities import atomicTfFromCalibration
from atom_core.atom import getTransform
from atom_core.dataset_io import saveAtomDataset


def cvStereoCalibrate(objp):
    # Arrays to store object points and image points from all the images.
    objpoints = []  # 3d point in real world space
    imgpoints_l = []  # 2d points in image plane.
    imgpoints_r = []  # 2d points in image plane.

    for collection_key, collection in dataset['collections'].items():
        # Find the chess board corners
        # TODO only works for first pattern
        first_pattern_key = list(dataset['calibration_config']['calibration_patterns'].keys())[0]
        n_points = int(dataset['calibration_config']['calibration_patterns'][first_pattern_key]['dimension']['x']) * \
            int(dataset['calibration_config']['calibration_patterns'][first_pattern_key]['dimension']['y'])
        image_points_r = np.ones((n_points, 2), np.float32)
        image_points_l = np.ones((n_points, 2), np.float32)

        for idx, point in enumerate(collection['labels'][right_camera]['idxs']):
            image_points_r[idx, 0] = point['x']
            image_points_r[idx, 1] = point['y']

        for idx, point in enumerate(collection['labels'][left_camera]['idxs']):
            image_points_l[idx, 0] = point['x']
            image_points_l[idx, 1] = point['y']

        imgpoints_l.append(image_points_l)
        imgpoints_r.append(image_points_r)
        objpoints.append(objp)

    # ---------------------------------------
    # --- Get intrinsic data for both sensors
    # ---------------------------------------
    # Source sensor
    K_r = np.zeros((3, 3), np.float32)
    D_r = np.zeros((5, 1), np.float32)
    K_r[0, :] = dataset['sensors'][right_camera]['camera_info']['K'][0:3]
    K_r[1, :] = dataset['sensors'][right_camera]['camera_info']['K'][3:6]
    K_r[2, :] = dataset['sensors'][right_camera]['camera_info']['K'][6:9]
    D_r[:, 0] = dataset['sensors'][right_camera]['camera_info']['D'][0:5]

    # Target sensor
    K_l = np.zeros((3, 3), np.float32)
    D_l = np.zeros((5, 1), np.float32)
    K_l[0, :] = dataset['sensors'][left_camera]['camera_info']['K'][0:3]
    K_l[1, :] = dataset['sensors'][left_camera]['camera_info']['K'][3:6]
    K_l[2, :] = dataset['sensors'][left_camera]['camera_info']['K'][6:9]
    D_l[:, 0] = dataset['sensors'][left_camera]['camera_info']['D'][0:5]

    height = dataset['sensors'][right_camera]['camera_info']['height']
    width = dataset['sensors'][right_camera]['camera_info']['width']
    image_size = (height, width)

    print('\n---------------------\n Starting stereo calibration ...')

    # Extrinsic stereo calibration
    stereocalib_criteria = (cv2.TERM_CRITERIA_MAX_ITER +
                            cv2.TERM_CRITERIA_EPS, 100, 1e-5)
    flags = (cv2.CALIB_USE_INTRINSIC_GUESS)

    ret, K_l, D_l, K_r, D_r, R, T, E, F = cv2.stereoCalibrate(objpoints, imgpoints_l,
                                                              imgpoints_r, K_l, D_l, K_r,
                                                              D_r, image_size,
                                                              criteria=stereocalib_criteria, flags=flags)

    print('\n---------------------\n Done!\n\n------\nCalibration results:\n------\n')

    print('K_left', K_l)
    print('D_left', D_l)
    print('K_right', K_r)
    print('D_right', D_r)
    print('R', R)
    print('T', T)
    print('E', E)
    print('F', F)

    camera_model = dict([('K_l', K_l), ('K_r', K_r), ('D_l', D_l),
                         ('D_r', D_r), ('R', R), ('T', T),
                         ('E', E), ('F', F)])

    cv2.destroyAllWindows()
    return camera_model


def getPatternConfig(dataset, pattern):
    # Pattern configs
    nx = dataset['calibration_config']['calibration_patterns'][pattern]['dimension']['x']
    ny = dataset['calibration_config']['calibration_patterns'][pattern]['dimension']['y']
    square = dataset['calibration_config']['calibration_patterns'][pattern]['size']
    inner_square = dataset['calibration_config']['calibration_patterns'][pattern]['inner_size']
    objp = np.zeros((nx * ny, 3), np.float32)
    objp[:, :2] = square * np.mgrid[0:nx, 0:ny].T.reshape(-1, 2)

    return nx, ny, square, inner_square, objp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-json", "--json_file", help="Json file containing train input dataset.", type=str,
                    required=True)
    ap.add_argument("-c", "--camera", help="Camera sensor name.", type=str, required=True)
    ap.add_argument("-si", "--show_images", help="If true the script shows images.", action='store_true', default=False)
    ap.add_argument("-p", "--pattern", help="Pattern to be used for calibration.", type=str, required=True)

    # Save args
    args = vars(ap.parse_args())
    json_file = args['json_file']
    camera = args['camera']
    show_images = args['show_images']
    pattern = args['pattern']


    # Read json file
    f = open(json_file, 'r')
    dataset = json.load(f)
    

    #########################################
    # DATASET PREPROCESSING
    #########################################

    nx, ny, square, inner_square, objp = getPatternConfig(dataset=dataset, pattern=pattern)

    # Remove partial detections (OpenCV does not support them)
    collections_to_delete = []
    # TODO only works for first pattern
    # first_pattern_key = list(dataset['calibration_config']['calibration_patterns'].keys())[0]
    number_of_corners = int(nx) * int(ny)
    for collection_key, collection in dataset['collections'].items():
        for sensor_key, sensor in dataset['sensors'].items():
            if sensor_key != camera:
                continue

            if sensor['msg_type'] == 'Image' and collection['labels'][pattern][sensor_key]['detected']:
                if not len(collection['labels'][pattern][sensor_key]['idxs']) == number_of_corners:
                    print(
                        Fore.RED + 'Partial detection removed:' + Style.RESET_ALL + ' label from collection ' +
                        collection_key + ', sensor ' + sensor_key)

                    collections_to_delete.append(collection_key)
                    break    

    for collection_key in collections_to_delete:
        del dataset['collections'][collection_key]

    # remove collections which do not have a pattern detection
    collections_to_delete = []
    for collection_key, collection in dataset['collections'].items():
        if not collection['labels'][pattern][args['camera']]['detected']:
            print('Collection ' + collection_key + ' pattern not detected on camera. Removing...')
            collections_to_delete.append(collection_key)

    for collection_key in collections_to_delete:
        del dataset['collections'][collection_key]

    print('\nUsing ' + str(len(dataset['collections'])) + ' collections.')
    
    exit(0)

if __name__ == '__main__':

    main()
    



    # # Compute OpenCV stereo calibration
    # calib_model = cvStereoCalibrate(objp)
    # R = calib_model['R']
    # t = calib_model['T']
    # K_r = calib_model['K_r']
    # D_r = calib_model['D_r']
    # K_l = calib_model['K_l']
    # D_l = calib_model['D_l']

    # # Extract homogeneous transformation between cameras
    # calib_tf = np.zeros((4, 4), np.float32)
    # calib_tf[0:3, 0:3] = R
    # calib_tf[0:3, 3] = t.ravel()
    # calib_tf[3, 0:3] = 0
    # calib_tf[3, 3] = 1

    # res = atomicTfFromCalibration(dataset, right_camera, left_camera, calib_tf)

    # # Re-write atomic transformation to the json file ...
    # dataset['sensors'][right_camera]['camera_info']['K'][0:3] = K_r[0, :]
    # dataset['sensors'][right_camera]['camera_info']['K'][3:6] = K_r[1, :]
    # dataset['sensors'][right_camera]['camera_info']['K'][6:9] = K_r[2, :]

    # dataset['sensors'][left_camera]['camera_info']['K'][0:3] = K_l[0, :]
    # dataset['sensors'][left_camera]['camera_info']['K'][3:6] = K_l[1, :]
    # dataset['sensors'][left_camera]['camera_info']['K'][6:9] = K_l[2, :]

    # dataset['sensors'][right_camera]['camera_info']['D'][0:5] = D_r[:, 0]
    # dataset['sensors'][left_camera]['camera_info']['D'][0:5] = D_l[:, 0]

    # child_link = dataset['calibration_config']['sensors'][left_camera]['child_link']
    # parent_link = dataset['calibration_config']['sensors'][left_camera]['parent_link']
    # frame = parent_link + '-' + child_link
    # quat = tf.transformations.quaternion_from_matrix(res)
    # for collection_key, collection in dataset['collections'].items():
    #     dataset['collections'][collection_key]['transforms'][frame]['quat'] = quat
    #     dataset['collections'][collection_key]['transforms'][frame]['trans'] = res[0:3, 3]

    # # Save results to a json file
    # filename_results_json = os.path.dirname(json_file) + '/cv_calibration.json'
    # saveAtomDataset(filename_results_json, dataset)
