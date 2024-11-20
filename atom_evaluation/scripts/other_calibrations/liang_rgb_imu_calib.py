#!/usr/bin/env python3

"""
Implementation of an ATOM-compatible alternative RGB-IMU calibration method described by Liang et. al (2024
"""

import argparse
from copy import deepcopy
import copy
import math
import random
import sys
from colorama import Fore
import numpy as np
import cv2
from prettytable import PrettyTable
from atom_calibration.collect import patterns
import tf
import scipy.linalg

from atom_core.dataset_io import filterCollectionsFromDataset, loadResultsJSON
from atom_core.atom import getTransform, getChain
from atom_core.geometry import matrixToTranslationRodrigues, matrixToTranslationRotation, translationRotationToTransform, traslationRodriguesToTransform
from atom_core.naming import generateKey
from atom_core.transformations import compareTransforms
from atom_core.utilities import atomError, compareAtomTransforms, createLambdaExpressionsForArgs


def getCameraIntrinsicsFromDataset(dataset, camera):
    # Camera intrinsics (from the dataset) needed to calculate B
    K = np.zeros((3, 3), np.float32)
    D = np.zeros((5, 1), np.float32)
    K[0, :] = dataset['sensors'][camera]['camera_info']['K'][0:3]
    K[1, :] = dataset['sensors'][camera]['camera_info']['K'][3:6]
    K[2, :] = dataset['sensors'][camera]['camera_info']['K'][6:9]
    D[:, 0] = dataset['sensors'][camera]['camera_info']['D'][0:5]

    height = dataset['sensors'][camera]['camera_info']['height']
    width = dataset['sensors'][camera]['camera_info']['width']
    image_size = (height, width)

    return K, D, image_size

def getPatternConfig(dataset, pattern):
    # Pattern configs
    nx = dataset['calibration_config']['calibration_patterns'][pattern]['dimension']['x']
    ny = dataset['calibration_config']['calibration_patterns'][pattern]['dimension']['y']
    square = dataset['calibration_config']['calibration_patterns'][pattern]['size']
    inner_square = dataset['calibration_config']['calibration_patterns'][pattern]['inner_size']
    objp = np.zeros((nx * ny, 3), np.float32)
    # set of coordinates (w.r.t. the pattern frame) of the corners
    objp[:, :2] = square * np.mgrid[0:nx, 0:ny].T.reshape(-1, 2)

    return nx, ny, square, inner_square, objp

def normalize_vector(vector):
    vector_norm = np.linalg.norm(vector)

    if  vector_norm != 0:
        normalized_vector = vector / vector_norm
    else:
        normalized_vector = vector # Zero-vector case

    return normalized_vector

def generate_skew_symmetric_matrix_from_vector(vector):

    skew_symmetric_matrix = np.zeros((3,3))

    skew_symmetric_matrix[0,1] = -vector[2]
    skew_symmetric_matrix[0,2] = vector[1]
    skew_symmetric_matrix[1,0] = vector[2]
    skew_symmetric_matrix[1,2] = -vector[0]
    skew_symmetric_matrix[2,0] = -vector[1]
    skew_symmetric_matrix[2,1] = vector[0]

    return skew_symmetric_matrix

def estimate_cam_to_imu(dataset, args, pattern, imu_link_name, world_link_name, K, D):
    
    # Calculate camera-to-pattern tf (c_T_p/H_cw in the paper) and imu_T_w for each collection
    c_T_p_lst = [] # list of tuples (collection, camera to pattern 4x4 transforms)
    imu_T_w_lst = []

    for collection_key, collection in dataset['collections'].items():

        # Pattern not detected by sensor in collection
        if not collection['labels'][args['pattern']][args['camera']]['detected']:
            continue        

        # Build a numpy array with the charuco corners
        corners = np.zeros(
            (len(collection['labels'][args['pattern']][args['camera']]['idxs']), 1, 2), dtype=float)
        ids = list(range(0, len(collection['labels'][args['pattern']][args['camera']]['idxs'])))
        for idx, point in enumerate(collection['labels'][args['pattern']][args['camera']]['idxs']):
            corners[idx, 0, 0] = point['x']
            corners[idx, 0, 1] = point['y']
            ids[idx] = point['id']

        # Find pose of the camera w.r.t the chessboard
        np_ids = np.array(ids, dtype=int)
        rvec, tvec = None, None
        _, rvec, tvec = cv2.aruco.estimatePoseCharucoBoard(np.array(corners, dtype=np.float32),
                                                            np_ids, pattern.board,
                                                            K, D, rvec, tvec)
        
        # Convert to 4x4 transform and add to list
        c_T_p = traslationRodriguesToTransform(tvec, rvec)
        c_T_p_lst.append((collection_key, c_T_p))
        
        
        # Get tf through FK. We can do this because the tfs in the dataset (in simulation) are GT.
        # TODO: Ultimately, this is not what we want to do. We are supposed to integrate IMU data to get the necessary TFs. This FK method is an intermediate step. Create an option for this to be enable for when IMU data integration is implemented.
        imu_T_w = getTransform(
            from_frame = imu_link_name,
            to_frame = world_link_name,
            transforms = collection['transforms']
        )

        imu_T_w_lst.append((collection_key, imu_T_w))

    # Get the interframe c_T_p (H_cij in the paper)
    # These will be stored in a dictionary where the key is name of the collections (i-j)
    interframe_tfs_dict = {}

    for i in range(len(c_T_p_lst) - 1):
        j = i+1
        key_name = str(c_T_p_lst[i][0]) + '-' + str(c_T_p_lst[j][0]) # Get the name of the key for the dict
        # Create an empty dict for each adjacent collection combination so it can hold both c_T and imu_T
        interframe_tfs_dict[key_name] = {}

        # Equation 17 from the original paper states that the tranformation matrix of the camera from collection i to collection j (c_T_ij) is equal to the c_T_p in collection i multiplied by its inverse in collection j
        c_T_p_i = c_T_p_lst[i][1]
        c_T_p_j_inv = np.linalg.inv(c_T_p_lst[j][1])
        interframe_c_T = np.dot(c_T_p_i, c_T_p_j_inv)

        # For now, we can determine the interframe imu transforms with a similar logic. imu_T_w_i * imu_T_w_j_inv == imu_T_ij
        imu_T_w_i = imu_T_w_lst[i][1]
        # print(imu_T_w_i)
        imu_T_w_j_inv = np.linalg.inv(imu_T_w_lst[j][1])
        # print(imu_T_w_j_inv)
        interframe_imu_T = np.dot(imu_T_w_i, imu_T_w_j_inv)

        # Save to the dict
        interframe_tfs_dict[key_name]["c_T"] = interframe_c_T
        interframe_tfs_dict[key_name]["imu_T"] = interframe_imu_T

    # print(interframe_tfs_dict)

    # With the adjacent collection combinations set up and the interframe tfs calculated, we can perform the calculations to determine the transformation between the camera and the IMU, c_T_imu (H_cg in the paper)

    tmp_i = 1
    # In this next for loop, collection combinations where the interframe rotation is null are filtered out. We need to keep a record of the collection combinations which were ignored to ignore once more when we iterate once again through this dictionary
    # NOTE: maybe using del would be cleaner
    collection_combination_keys_to_ignore = []
    for collection_combination_key, collection_combination in interframe_tfs_dict.items():

        # Get Rotation Matrices
        imu_T_ij = collection_combination["imu_T"]
        c_T_ij = collection_combination["c_T"]

        imu_R_ij = imu_T_ij[:3,:3]
        c_R_ij = c_T_ij[:3,:3]

        # Get Rodrigues vectors
        imu_r_ij, _ = cv2.Rodrigues(imu_R_ij)
        c_r_ij, _ = cv2.Rodrigues(c_R_ij)            

        # Calculate the norms and normalize
        norm_imu_r_ij = np.linalg.norm(imu_r_ij)
        norm_c_r_ij = np.linalg.norm(c_r_ij)
        
        if (norm_imu_r_ij == 0 or norm_c_r_ij == 0):
            print("Null rotation in collection combination " + collection_combination_key + "... Ignoring...")
            collection_combination_keys_to_ignore.append(collection_combination_key)
            continue

        normalized_imu_r_ij = imu_r_ij / norm_imu_r_ij
        normalized_c_r_ij = c_r_ij / norm_c_r_ij

        # Calculate imu_P_ij and c_P_ij
        imu_P_ij = 2 * np.sin(norm_imu_r_ij / 2) * normalized_imu_r_ij
        c_P_ij = 2 * np.sin(norm_c_r_ij / 2) * normalized_c_r_ij

        tmp_A = generate_skew_symmetric_matrix_from_vector(imu_P_ij + c_P_ij)
        tmp_b = c_P_ij - imu_P_ij

        if tmp_i == 1:
            A = tmp_A
            b = tmp_b
        else:
            A = np.vstack((A, tmp_A))
            b = np.vstack((b, tmp_b))

        tmp_i += 1
        
    # Solving equation (23)
    pinv_A = np.linalg.pinv(A)
    c_P_imu_prime = pinv_A.dot(b)
    
    c_P_imu = (2 * c_P_imu_prime) / np.sqrt(1 + np.linalg.norm(c_P_imu_prime)**2)
    c_P_imu_transposed = c_P_imu.T

    c_R_imu = (1 - ((np.linalg.norm(c_P_imu)**2)/2)) * np.eye(3) + (1/2) * (c_P_imu.dot(c_P_imu_transposed) + np.sqrt(4 - np.linalg.norm(c_P_imu)**2) * generate_skew_symmetric_matrix_from_vector(c_P_imu))

    tmp_i = 1
    for collection_combination_key, collection_combination in interframe_tfs_dict.items():
        
        # Ignore collection combinations that were ignored in the previous for loop
        if collection_combination_key in collection_combination_keys_to_ignore:
            continue

        # Get Rotation Matrices
        imu_T_ij = collection_combination["imu_T"]
        c_T_ij = collection_combination["c_T"]

        imu_R_ij = imu_T_ij[:3,:3]
        c_R_ij = c_T_ij[:3,:3]

        # Get translation matrices
        imu_t_ij = imu_T_ij[:3,3:4]
        c_t_ij = c_T_ij[:3,3:4]

        tmp_AA = imu_R_ij - np.eye(3)
        tmp_bb = c_R_imu.dot(c_t_ij) - imu_t_ij

        if tmp_i == 1:
            AA = tmp_AA
            bb = tmp_bb
        else:
            AA = np.vstack((AA, tmp_AA))
            bb = np.vstack((bb, tmp_bb))

        tmp_i += 1

    # Solving equation (27)
    pinv_AA = np.linalg.pinv(AA)
    c_t_imu = pinv_AA.dot(bb)

    c_T_imu = np.zeros((4,4))
    c_T_imu[:3,:3] = c_R_imu
    c_T_imu[:3,3:4] = c_t_imu
    c_T_imu[3,:] = [0, 0, 0, 1]

    return c_T_imu, interframe_tfs_dict

def main():

    ########################################
    # ARGUMENT PARSER #
    ########################################

    # Parse command line arguments
    ap = argparse.ArgumentParser()
    ap.add_argument("-json", "--json_file", type=str, required=True,
                    help="Json file containing input dataset.")
    ap.add_argument("-csf", "--collection_selection_function", default=None, type=str,
                    help="A string to be evaluated into a lambda function that receives a collection name as input and "
                    "returns True or False to indicate if the collection should be loaded (and used in the "
                    "optimization). The Syntax is lambda name: f(x), where f(x) is the function in python "
                    "language. Example: lambda name: int(name) > 5 , to load only collections 6, 7, and onward.")
    ap.add_argument("-c", "--camera", help="Camera sensor name.", type=str, required=True)
    ap.add_argument("-iln", "--imu_link_name", help="The name of the IMU link.", type=str, required=True)
    ap.add_argument("-wln", "--world_link_name", help="Name of the world coordinate frame.", type=str, required=False, default='world')
    ap.add_argument("-p", "--pattern", help="Pattern to be used for calibration.", type=str, required=True)
    ap.add_argument("-uic", "--use_incomplete_collections", action="store_true", default=False, help="Remove any collection which does not have a detection for all sensors.", )
    ap.add_argument("-ctgt", "--compare_to_ground_truth", action="store_true", help="If the system being calibrated is simulated, directly compare the TFs to the ground truth.")
 
    # Roslaunch adds two arguments (__name and __log) that break our parser. Lets remove those.
    arglist = [x for x in sys.argv[1:] if not x.startswith("__")]
    # these args have the selection functions as strings
    args_original = vars(ap.parse_args(args=arglist))
    args = createLambdaExpressionsForArgs(args_original)  # selection functions are now lambdas

    json_file = args['json_file']
    collection_selection_function = args['collection_selection_function']
    imu_link_name = args["imu_link_name"]
    camera = args['camera']
    pattern = args['pattern']
    world_link_name = args['world_link_name']

    # Read dataset file
    dataset, json_file = loadResultsJSON(json_file, collection_selection_function)
    args['remove_partial_detections'] = True
    dataset = filterCollectionsFromDataset(dataset, args)
    
    dataset_ground_truth = deepcopy(dataset)  # make a copy before adding noise
    dataset_initial = deepcopy(dataset)  # store initial values

    # ---------------------------------------
    # --- Define selected collection key.
    # ---------------------------------------
    # We only need to get one collection because optimized transformations are static, which means they are the same for all collections. Let's select the first key in the dictionary and always get that transformation.
    selected_collection_key = list(dataset["collections"].keys())[0]
    print("Selected collection key is " + str(selected_collection_key))

    # ---------------------------------------
    # Verifications
    # ---------------------------------------

    # Check that the camera has rgb modality
    if not dataset['sensors'][args['camera']]['modality'] == 'rgb':
        atomError('Sensor ' + args['camera'] + ' is not of rgb modality.')

    # ---------------------------------------
    # Pattern configuration
    # ---------------------------------------
    nx = dataset['calibration_config']['calibration_patterns'][args['pattern']
                                                               ]['dimension']['x']
    ny = dataset['calibration_config']['calibration_patterns'][args['pattern']
                                                               ]['dimension']['y']
    square = dataset['calibration_config']['calibration_patterns'][args['pattern']]['size']
    pts_3d = np.zeros((nx * ny, 3), np.float32)
    # set of coordinates (w.r.t. the pattern frame) of the corners
    pts_3d[:, :2] = square * np.mgrid[0:nx, 0:ny].T.reshape(-1, 2)
    # print(pts_3d)
    number_of_corners = int(nx) * int(ny)

    # Initialization of a board object for pose estimation
    board_size = {'x': nx, 'y': ny}
    inner_length = dataset['calibration_config']['calibration_patterns'][
        args['pattern']]['inner_size']
    dictionary = dataset['calibration_config']['calibration_patterns'][
        args['pattern']]['dictionary']
    pattern = patterns.CharucoPattern(board_size, square, inner_length, dictionary)
    
    
    # ---------------------------------------
    # --- Get intrinsic data for the camera
    # ---------------------------------------
    # Source sensor
    K = np.zeros((3, 3), np.float32)
    D = np.zeros((5, 1), np.float32)
    K[0, :] = dataset['sensors'][args['camera']]['camera_info']['K'][0:3]
    K[1, :] = dataset['sensors'][args['camera']]['camera_info']['K'][3:6]
    K[2, :] = dataset['sensors'][args['camera']]['camera_info']['K'][6:9]
    D[:, 0] = dataset['sensors'][args['camera']]['camera_info']['D'][0:5]


    # ---------------------------------------
    # --- Implementation
    # ---------------------------------------

    # RANSAC parameters
    iter_num = 10
    threshold = 5
    num_samples = 10

    # We need to first calculate the psuedo-ground-truth c_T_imu value against which we will compare the others. This one is calculated with every available collection.

    c_T_imu, _ = estimate_cam_to_imu(
        dataset=dataset,
        args=args,
        pattern=pattern,
        imu_link_name=imu_link_name,
        world_link_name=world_link_name,
        K=K,
        D=D
    )


    max_inliers = 0
    for ransac_iteration in range(iter_num):
        
        print("\n#####################################\n# RANSAC iteration " + str(ransac_iteration) + "...\n#####################################\n")

        # Create a copy of the original dataset
        dataset_to_use = copy.deepcopy(dataset)
    
        # Pick random collections to be deleted
        num_samples_deleted = len(dataset_to_use["collections"]) - num_samples
        collections_to_delete = random.sample(dataset_to_use["collections"].keys(), num_samples_deleted)
    
        for c in collections_to_delete:
            del dataset_to_use["collections"][c]

        estimated_c_T_imu, interframe_tfs_dict = estimate_cam_to_imu(
            dataset=dataset_to_use,
            args=args,
            pattern=pattern,
            imu_link_name=imu_link_name,
            world_link_name=world_link_name,
            K=K,
            D=D
            )

        # c_T_lst = []
        inliers = 0
        for collection_combination_key, collection_combination in interframe_tfs_dict.items():
            c_T = collection_combination["c_T"]
            imu_T = collection_combination["imu_T"]

            estimated_imu_T = estimated_c_T_imu @ c_T @ np.linalg.inv(estimated_c_T_imu)            
            estimated_imu_t = estimated_imu_T[:3, 3:4]

            imu_t = imu_T[:3, 3:4]

            error = np.linalg.norm(estimated_imu_t - imu_t)
            
            if error <= threshold:
                inliers +=1

        if inliers >= max_inliers:
            max_inliers = inliers
            best_estimated_c_T_imu = estimated_c_T_imu

    print(best_estimated_c_T_imu)


if __name__ == "__main__":
    main()