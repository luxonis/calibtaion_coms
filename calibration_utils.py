#!/usr/bin/env python3

import cv2
import glob
import os
import shutil
import numpy as np
from scipy.spatial.transform import Rotation
import time
import json
import cv2.aruco as aruco
from pathlib import Path
from functools import reduce
from collections import deque
from typing import Optional
# Creates a set of 13 polygon coordinates
rectProjectionMode = 0

colors = [(0, 255 , 0), (0, 0, 255)]
def setPolygonCoordinates(height, width):
    horizontal_shift = width//4
    vertical_shift = height//4

    margin = 60
    slope = 150

    p_coordinates = [
        [[margin, margin], [margin, height-margin],
            [width-margin, height-margin], [width-margin, margin]],

        [[margin, 0], [margin, height], [width//2, height-slope], [width//2, slope]],
        [[horizontal_shift, 0], [horizontal_shift, height], [
            width//2 + horizontal_shift, height-slope], [width//2 + horizontal_shift, slope]],
        [[horizontal_shift*2-margin, 0], [horizontal_shift*2-margin, height], [width//2 +
                                                                               horizontal_shift*2-margin, height-slope], [width//2 + horizontal_shift*2-margin, slope]],

        [[width-margin, 0], [width-margin, height],
            [width//2, height-slope], [width//2, slope]],
        [[width-horizontal_shift, 0], [width-horizontal_shift, height], [width //
                                                                         2-horizontal_shift, height-slope], [width//2-horizontal_shift, slope]],
        [[width-horizontal_shift*2+margin, 0], [width-horizontal_shift*2+margin, height], [width //
                                                                                           2-horizontal_shift*2+margin, height-slope], [width//2-horizontal_shift*2+margin, slope]],

        [[0, margin], [width, margin], [
            width-slope, height//2], [slope, height//2]],
        [[0, vertical_shift], [width, vertical_shift], [width-slope,
                                                        height//2+vertical_shift], [slope, height//2+vertical_shift]],
        [[0, vertical_shift*2-margin], [width, vertical_shift*2-margin], [width-slope,
                                                                          height//2+vertical_shift*2-margin], [slope, height//2+vertical_shift*2-margin]],

        [[0, height-margin], [width, height-margin],
         [width-slope, height//2], [slope, height//2]],
        [[0, height-vertical_shift], [width, height-vertical_shift], [width -
                                                                      slope, height//2-vertical_shift], [slope, height//2-vertical_shift]],
        [[0, height-vertical_shift*2+margin], [width, height-vertical_shift*2+margin], [width -
                                                                                        slope, height//2-vertical_shift*2+margin], [slope, height//2-vertical_shift*2+margin]]
    ]
    return p_coordinates


def getPolygonCoordinates(idx, p_coordinates):
    return p_coordinates[idx]


def getNumOfPolygons(p_coordinates):
    return len(p_coordinates)

# Filters polygons to just those at the given indexes.


def select_polygon_coords(p_coordinates, indexes):
    if indexes == None:
        # The default
        return p_coordinates
    else:
        print("Filtering polygons to those at indexes=", indexes)
        return [p_coordinates[i] for i in indexes]


def image_filename(polygon_index, total_num_of_captured_images):
    return "p{polygon_index}_{total_num_of_captured_images}.png".format(polygon_index=polygon_index, total_num_of_captured_images=total_num_of_captured_images)


def polygon_from_image_name(image_name):
    """Returns the polygon index from an image name (ex: "left_p10_0.png" => 10)"""
    return int(re.findall("p(\d+)", image_name)[0])


class StereoCalibration(object):
    """Class to Calculate Calibration and Rectify a Stereo Camera."""

    def __init__(self, traceLevel: float = 1.0, outputScaleFactor: float = 0.5, disableCamera: list = []):
        self.traceLevel = traceLevel
        self.output_scale_factor = outputScaleFactor
        self.disableCamera = disableCamera

        """Class to Calculate Calibration and Rectify a Stereo Camera."""

    def calibrate(self, board_config, filepath, square_size, mrk_size, squaresX, squaresY, camera_model, enable_disp_rectify):
        """Function to calculate calibration for stereo camera."""
        start_time = time.time()
        # init object data
        if self.traceLevel == 2 or self.traceLevel == 10:
            print(f'squareX is {squaresX}')
        self.enable_rectification_disp = enable_disp_rectify
        self.cameraModel = camera_model
        self.data_path = filepath
        self.aruco_dictionary = aruco.Dictionary_get(aruco.DICT_4X4_1000)

        self.board = aruco.CharucoBoard_create(
            # 22, 16,
            squaresX, squaresY,
            square_size,
            mrk_size,
            self.aruco_dictionary)

        # parameters = aruco.DetectorParameters_create()
        combinedCoverageImage = None
        resizeWidth, resizeHeight = 0, 0
        assert mrk_size != None,  "ERROR: marker size not set"
        for camera in board_config['cameras'].keys():
            cam_info = board_config['cameras'][camera]
            if cam_info["name"] not in self.disableCamera:
                images_path = filepath + '/' + cam_info['name']
                image_files = glob.glob(images_path + "/*")
                image_files.sort()
                for im in image_files:
                    frame = cv2.imread(im)
                    height, width, _ = frame.shape
                    widthRatio = resizeWidth / width
                    heightRatio = resizeHeight / height
                    if (widthRatio > 0.8 and heightRatio > 0.8 and widthRatio <= 1.0 and heightRatio <= 1.0) or (widthRatio > 1.2 and heightRatio > 1.2) or (resizeHeight == 0):
                        resizeWidth = width
                        resizeHeight = height
                    break
        for camera in board_config['cameras'].keys():
            cam_info = board_config['cameras'][camera]
            if cam_info["name"] not in self.disableCamera:
                print(
                    '<------------Calibrating {} ------------>'.format(cam_info['name']))
                images_path = filepath + '/' + cam_info['name']
                ret, intrinsics, dist_coeff, _, _, size, coverageImage = self.calibrate_intrinsics(
                    images_path, cam_info['hfov'])
                cam_info['intrinsics'] = intrinsics
                cam_info['dist_coeff'] = dist_coeff
                cam_info['size'] = size # (Width, height)
                cam_info['reprojection_error'] = ret
                print("Reprojection error of {0}: {1}".format(
                    cam_info['name'], ret))
                if self.traceLevel == 3 or self.traceLevel == 10:
                    print("Estimated intrinsics of {0}: \n {1}".format(
                    cam_info['name'], intrinsics))

                coverage_name = cam_info['name']
                print_text = f'Coverage Image of {coverage_name} with reprojection error of {round(ret,5)}'
                height, width, _ = coverageImage.shape

                if width > resizeWidth and height > resizeHeight:
                    coverageImage = cv2.resize(
                    coverageImage, (0, 0), fx= resizeWidth / width, fy= resizeWidth / width)

                height, width, _ = coverageImage.shape
                if height > resizeHeight:
                    height_offset = (height - resizeHeight)//2
                    coverageImage = coverageImage[height_offset:height_offset+resizeHeight, :]

                height, width, _ = coverageImage.shape
                height_offset = (resizeHeight - height)//2
                width_offset = (resizeWidth - width)//2
                subImage = np.pad(coverageImage, ((height_offset, height_offset), (width_offset, width_offset), (0, 0)), 'constant', constant_values=0)
                cv2.putText(subImage, print_text, (50, 50+height_offset), cv2.FONT_HERSHEY_SIMPLEX, 2*coverageImage.shape[0]/1750, (0, 0, 0), 2)
                if combinedCoverageImage is None:
                    combinedCoverageImage = subImage
                else:
                    combinedCoverageImage = np.hstack((combinedCoverageImage, subImage))
                coverage_file_path = filepath + '/' + coverage_name + '_coverage.png'
                cv2.imwrite(coverage_file_path, subImage)

        combinedCoverageImage = cv2.resize(combinedCoverageImage, (0, 0), fx=self.output_scale_factor, fy=self.output_scale_factor)
        if enable_disp_rectify:
            cv2.imshow('coverage image', combinedCoverageImage)
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        
        for camera in board_config['cameras'].keys():
            left_cam_info = board_config['cameras'][camera]
            if str(left_cam_info["name"]) not in self.disableCamera:
                if 'extrinsics' in left_cam_info:
                    if 'to_cam' in left_cam_info['extrinsics']:
                        left_cam = camera
                        right_cam = left_cam_info['extrinsics']['to_cam']
                        left_path = filepath + '/' + left_cam_info['name']
    
                        right_cam_info = board_config['cameras'][left_cam_info['extrinsics']['to_cam']]
                        if str(right_cam_info["name"]) not in self.disableCamera:
                            right_path = filepath + '/' + right_cam_info['name']
                            print('<-------------Extrinsics calibration of {} and {} ------------>'.format(
                                left_cam_info['name'], right_cam_info['name']))
    
                            specTranslation = left_cam_info['extrinsics']['specTranslation']
                            rot = left_cam_info['extrinsics']['rotation']
    
                            translation = np.array(
                                [specTranslation['x'], specTranslation['y'], specTranslation['z']], dtype=np.float32)
                            rotation = Rotation.from_euler(
                                'xyz', [rot['r'], rot['p'], rot['y']], degrees=True).as_matrix().astype(np.float32)
    
                            extrinsics = self.calibrate_extrinsics(left_path, right_path, left_cam_info['intrinsics'], left_cam_info[
                                                                   'dist_coeff'], right_cam_info['intrinsics'], right_cam_info['dist_coeff'], translation, rotation)
                            if extrinsics[0] == -1:
                                return -1, extrinsics[1]
    
                            if board_config['stereo_config']['left_cam'] == left_cam and board_config['stereo_config']['right_cam'] == right_cam:
                                board_config['stereo_config']['rectification_left'] = extrinsics[3]
                                board_config['stereo_config']['rectification_right'] = extrinsics[4]
                                board_config['stereo_config']['p_left'] = extrinsics[5]
                                board_config['stereo_config']['p_right'] = extrinsics[6]
                            elif board_config['stereo_config']['left_cam'] == right_cam and board_config['stereo_config']['right_cam'] == left_cam:
                                board_config['stereo_config']['rectification_left'] = extrinsics[4]
                                board_config['stereo_config']['rectification_right'] = extrinsics[3]
                                board_config['stereo_config']['p_left'] = extrinsics[6]
                                board_config['stereo_config']['p_right'] = extrinsics[5]
    
                            """ for stereoObj in board_config['stereo_config']:
    
                                if stereoObj['left_cam'] == left_cam and stereoObj['right_cam'] == right_cam and stereoObj['main'] == 1:
                                    stereoObj['rectification_left'] = extrinsics[3]
                                    stereoObj['rectification_right'] = extrinsics[4] """
    
                            print('<-------------Epipolar error of {} and {} ------------>'.format(
                                left_cam_info['name'], right_cam_info['name']))

                            left_cam_info['extrinsics']['epipolar_error'] = self.test_epipolar_charuco(
                                                                                            left_path, 
                                                                                            right_path, 
                                                                                            left_cam_info['intrinsics'], 
                                                                                            left_cam_info['dist_coeff'], 
                                                                                            right_cam_info['intrinsics'], 
                                                                                            right_cam_info['dist_coeff'], 
                                                                                            extrinsics[2], # Translation between left and right Cameras
                                                                                            extrinsics[3], # Left Rectification rotation 
                                                                                            extrinsics[4], # Right Rectification rotation 
                                                                                            extrinsics[5], # Left Rectification Intrinsics
                                                                                            extrinsics[6]) # Right Rectification Intrinsics
    
                            left_cam_info['extrinsics']['rotation_matrix'] = extrinsics[1]
                            left_cam_info['extrinsics']['translation'] = extrinsics[2]
                            left_cam_info['extrinsics']['stereo_error'] = extrinsics[0]
    
        return 1, board_config

    def draw_corners(self, charuco_corners, displayframe):
        for corners in charuco_corners:
            color = (int(np.random.randint(0, 255)), int(np.random.randint(0, 255)), int(np.random.randint(0, 255)))
            for corner in corners:
                corner_int = (int(corner[0][0]), int(corner[0][1]))
                cv2.circle(displayframe, corner_int, 4, color, -1)
        height, width = displayframe.shape[:2]
        start_point = (0, 0)  # top of the image
        end_point = (0, height)

        color = (0, 0, 0)  # blue in BGR
        thickness = 4

        # Draw the line on the image
        cv2.line(displayframe, start_point, end_point, color, thickness)
        return displayframe
    
    def detect_charuco_board(self, image: np.array):
        arucoParams = cv2.aruco.DetectorParameters_create()
        arucoParams.minMarkerDistanceRate = 0.01
        corners, ids, rejectedImgPoints = cv2.aruco.detectMarkers(image, self.aruco_dictionary, parameters=arucoParams)  # First, detect markers
        marker_corners, marker_ids, refusd, recoverd = cv2.aruco.refineDetectedMarkers(image, self.board, corners, ids, rejectedCorners=rejectedImgPoints)
        # If found, add object points, image points (after refining them)
        if len(marker_corners)>0:
            ret, corners, ids = cv2.aruco.interpolateCornersCharuco(marker_corners,marker_ids,image, self.board, minMarkers = 1)
            return ret, corners, ids, marker_corners, marker_ids
        else:
            return None

    def analyze_charuco(self, images, scale_req=False, req_resolution=(800, 1280)):
        """
        Charuco base pose estimation.
        """
        # print("POSE ESTIMATION STARTS:")
        allCorners = []
        allIds = []
        all_marker_corners = []
        all_marker_ids = []
        all_recovered = []
        # decimator = 0
        # SUB PIXEL CORNER DETECTION CRITERION
        criteria = (cv2.TERM_CRITERIA_EPS +
                    cv2.TERM_CRITERIA_MAX_ITER, 10000, 0.00001)
        count = 0
        skip_vis = False
        for im in images:
            if self.traceLevel == 3 or self.traceLevel == 10:
                print("=> Processing image {0}".format(im))
            img_pth = Path(im)
            frame = cv2.imread(im)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            expected_height = gray.shape[0]*(req_resolution[1]/gray.shape[1])

            if scale_req and not (gray.shape[0] == req_resolution[0] and gray.shape[1] == req_resolution[1]):
                if int(expected_height) == req_resolution[0]:
                    # resizing to have both stereo and rgb to have same
                    # resolution to capture extrinsics of the rgb-right camera
                    gray = cv2.resize(gray, req_resolution[::-1],
                                      interpolation=cv2.INTER_CUBIC)
                else:
                    # resizing and cropping to have both stereo and rgb to have same resolution
                    # to calculate extrinsics of the rgb-right camera
                    scale_width = req_resolution[1]/gray.shape[1]
                    dest_res = (
                        int(gray.shape[1] * scale_width), int(gray.shape[0] * scale_width))
                    gray = cv2.resize(
                        gray, dest_res, interpolation=cv2.INTER_CUBIC)
                    if gray.shape[0] < req_resolution[0]:
                        raise RuntimeError("resizeed height of rgb is smaller than required. {0} < {1}".format(
                            gray.shape[0], req_resolution[0]))
                    # print(gray.shape[0] - req_resolution[0])
                    del_height = (gray.shape[0] - req_resolution[0]) // 2
                    # gray = gray[: req_resolution[0], :]
                    gray = gray[del_height: del_height + req_resolution[0], :]

                count += 1
            
            ret, charuco_corners, charuco_ids, marker_corners, marker_ids  = self.detect_charuco_board(gray)

            if self.traceLevel == 2 or self.traceLevel == 4 or self.traceLevel == 10:
                print('{0} number of Markers corners detected in the image {1}'.format(
                    len(charuco_corners), img_pth.name))

            if charuco_corners is not None and charuco_ids is not None and len(charuco_corners) > 3:

                cv2.cornerSubPix(gray, charuco_corners,
                                    winSize=(5, 5),
                                    zeroZone=(-1, -1),
                                    criteria=criteria)
                allCorners.append(charuco_corners)  # Charco chess corners
                allIds.append(charuco_ids)  # charuco chess corner id's
                all_marker_corners.append(marker_corners)
                all_marker_ids.append(marker_ids)
            else:
                print(im)
                raise RuntimeError("Failed to detect markers in the image")

            if self.traceLevel == 2 or self.traceLevel == 4 or self.traceLevel == 10:
                rgb_img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                cv2.aruco.drawDetectedMarkers(rgb_img, marker_corners, marker_ids, (0, 0, 255))
                cv2.aruco.drawDetectedCornersCharuco(rgb_img, charuco_corners, charuco_ids, (0, 255, 0))

                if rgb_img.shape[1] > 1920:
                    rgb_img = cv2.resize(rgb_img, (0, 0), fx=0.7, fy=0.7)
                if not skip_vis:
                    name = img_pth.name + ' - ' + "marker frame"
                    cv2.imshow(name, rgb_img)
                    k = cv2.waitKey(0)
                    if k == 27: # Esc key to skip vis
                        skip_vis = True
                cv2.destroyAllWindows()
        # imsize = gray.shape[::-1]
        return allCorners, allIds, all_marker_corners, all_marker_ids, gray.shape[::-1], all_recovered

    def calibrate_intrinsics(self, image_files, hfov):
        image_files = glob.glob(image_files + "/*")
        image_files.sort()
        assert len(
            image_files) != 0, "ERROR: Images not read correctly, check directory"

        allCorners, allIds, _, _, imsize, _ = self.analyze_charuco(image_files)

        coverageImage = np.ones(imsize[::-1], np.uint8) * 255
        coverageImage = cv2.cvtColor(coverageImage, cv2.COLOR_GRAY2BGR)
        coverageImage = self.draw_corners(allCorners, coverageImage)

        if self.cameraModel == 'perspective':
            ret, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors = self.calibrate_camera_charuco(
                allCorners, allIds, imsize, hfov)
            # (Height, width)
            if self.traceLevel == 4 or self.traceLevel == 5 or self.traceLevel == 10:
                self.undistort_visualization(
                    image_files, camera_matrix, distortion_coefficients, imsize)

            return ret, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors, imsize, coverageImage
        else:
            print('Fisheye--------------------------------------------------')
            ret, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors = self.calibrate_fisheye(
                allCorners, allIds, imsize, hfov)
            if self.traceLevel == 4 or self.traceLevel == 5 or self.traceLevel == 10:
                self.undistort_visualization(
                    image_files, camera_matrix, distortion_coefficients, imsize)
            print('Fisheye rotation vector', rotation_vectors[0])
            print('Fisheye translation vector', translation_vectors[0])

            # (Height, width)
            return ret, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors, imsize, coverageImage

    def calibrate_extrinsics(self, images_left, images_right, M_l, d_l, M_r, d_r, guess_translation, guess_rotation):
        self.objpoints = []  # 3d point in real world space
        self.imgpoints_l = []  # 2d points in image plane.
        self.imgpoints_r = []  # 2d points in image plane.

        images_left = glob.glob(images_left + "/*")
        images_right = glob.glob(images_right + "/*")

        images_left.sort()
        images_right.sort()

        assert len(
            images_left) != 0, "ERROR: Images not found, check directory"
        assert len(
            images_right) != 0, "ERROR: Images not found, check directory"

        scale = None
        scale_req = False
        frame_left_shape = cv2.imread(images_left[0], 0).shape # (h,w)
        frame_right_shape = cv2.imread(images_right[0], 0).shape
        scalable_res = frame_left_shape
        scaled_res = frame_right_shape

        if frame_right_shape[0] < frame_left_shape[0] and frame_right_shape[1] < frame_left_shape[1]:
            scale_req = True
            scale = frame_right_shape[1] / frame_left_shape[1]
        elif frame_right_shape[0] > frame_left_shape[0] and frame_right_shape[1] > frame_left_shape[1]:
            scale_req = True
            scale = frame_left_shape[1] / frame_right_shape[1]
            scalable_res = frame_right_shape
            scaled_res = frame_left_shape

        if scale_req:
            scaled_height = scale * scalable_res[0]
            diff = scaled_height - scaled_res[0]
            # if scaled_height <  smaller_res[0]:
            if diff < 0:
                scaled_res = (int(scaled_height), scaled_res[1])
        if self.traceLevel == 3 or self.traceLevel == 10:
            print(
                f'Is scale Req: {scale_req}\n scale value: {scale} \n scalable Res: {scalable_res} \n scale Res: {scaled_res}')
            print("Original res Left :{}".format(frame_left_shape))
            print("Original res Right :{}".format(frame_right_shape))
            print("Scale res :{}".format(scaled_res))

        # scaled_res = (scaled_height, )
        M_lp = self.scale_intrinsics(M_l, frame_left_shape, scaled_res)
        M_rp = self.scale_intrinsics(M_r, frame_right_shape, scaled_res)

        # print("~~~~~~~~~~~ POSE ESTIMATION LEFT CAMERA ~~~~~~~~~~~~~")
        allCorners_l, allIds_l, _, _, imsize_l, _ = self.analyze_charuco(
            images_left, scale_req, scaled_res)

        # print("~~~~~~~~~~~ POSE ESTIMATION RIGHT CAMERA ~~~~~~~~~~~~~")
        allCorners_r, allIds_r, _, _, imsize_r, _ = self.analyze_charuco(
            images_right, scale_req, scaled_res)
        if self.traceLevel == 3 or self.traceLevel == 10:
            print(f'Image size of right side (w, h): {imsize_r}')
            print(f'Image size of left side (w, h): {imsize_l}')

        assert imsize_r == imsize_l, "Left and right resolution scaling is wrong"

        return self.calibrate_stereo(
            allCorners_l, allIds_l, allCorners_r, allIds_r, imsize_r, M_lp, d_l, M_rp, d_r, guess_translation, guess_rotation)

    def scale_intrinsics(self, intrinsics, originalShape, destShape):
        scale = destShape[1] / originalShape[1] # scale on width
        scale_mat = np.array([[scale, 0, 0], [0, scale, 0], [0, 0, 1]])
        scaled_intrinsics = np.matmul(scale_mat, intrinsics)
        """ print("Scaled height offset : {}".format(
            (originalShape[0] * scale - destShape[0]) / 2))
        print("Scaled width offset : {}".format(
            (originalShape[1] * scale - destShape[1]) / 2)) """
        scaled_intrinsics[1][2] -= (originalShape[0]      # c_y - along height of the image
                                    * scale - destShape[0]) / 2
        scaled_intrinsics[0][2] -= (originalShape[1]     # c_x width of the image
                                    * scale - destShape[1]) / 2
        if self.traceLevel == 3 or self.traceLevel == 10:
            print('original_intrinsics')
            print(intrinsics)
            print('scaled_intrinsics')
            print(scaled_intrinsics)

        return scaled_intrinsics

    def undistort_visualization(self, img_list, K, D, img_size):
        for im in img_list:
            # print(im)
            img = cv2.imread(im)
            # h, w = img.shape[:2]
            if self.cameraModel == 'perspective':
                kScaled, _ = cv2.getOptimalNewCameraMatrix(K, D, img_size, 0)
                # print(f'K scaled is \n {kScaled} and size is \n {img_size}')
                # print(f'D Value is \n {D}')
                map1, map2 = cv2.initUndistortRectifyMap(
                    K, D, np.eye(3), kScaled, img_size, cv2.CV_32FC1)
            else:
                map1, map2 = cv2.fisheye.initUndistortRectifyMap(
                    K, D, np.eye(3), K, img_size, cv2.CV_32FC1)

            undistorted_img = cv2.remap(
                img, map1, map2, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            cv2.imshow("undistorted", undistorted_img)
            if self.traceLevel == 3 or self.traceLevel == 10:
                print(f'image path - {im}')
                print(f'Image Undistorted Size {undistorted_img.shape}')
            k = cv2.waitKey(0)
            if k == 27:  # Esc key to stop
                break
        cv2.destroyWindow("undistorted")


    def calibrate_camera_charuco(self, allCorners, allIds, imsize, hfov):
        """
        Calibrates the camera using the dected corners.
        """
        f = imsize[0] / (2 * np.tan(np.deg2rad(hfov/2)))
        # TODO(sachin): Change the initialization to be initialized using the guess from fov
        print("INTRINSIC CALIBRATION")
        cameraMatrixInit = np.array([[f,    0.0,      imsize[0]/2],
                                     [0.0,     f,      imsize[1]/2],
                                     [0.0,   0.0,        1.0]])

        # cameraMatrixInit = np.array([[857.1668,    0.0,      643.9126],
        #                                  [0.0,     856.0823,  387.56018],
        #                                  [0.0,        0.0,        1.0]])
        """ if imsize[1] < 700:
            cameraMatrixInit = np.array([[400.0,    0.0,      imsize[0]/2],
                                         [0.0,     400.0,  imsize[1]/2],
                                         [0.0,        0.0,        1.0]])
        elif imsize[1] < 1100:
            cameraMatrixInit = np.array([[857.1668,    0.0,      643.9126],
                                         [0.0,     856.0823,  387.56018],
                                         [0.0,        0.0,        1.0]])
        else:
            cameraMatrixInit = np.array([[3819.8801,    0.0,     1912.8375],
                                         [0.0,     3819.8801, 1135.3433],
                                         [0.0,        0.0,        1.]]) """
        if self.traceLevel == 3 or self.traceLevel == 10:
            print(
                f'Camera Matrix initialization with HFOV of {hfov} is.............')
            print(cameraMatrixInit)

        distCoeffsInit = np.zeros((5, 1))
        flags = (cv2.CALIB_USE_INTRINSIC_GUESS + 
                 cv2.CALIB_RATIONAL_MODEL)

    #     flags = (cv2.CALIB_RATIONAL_MODEL)
        (ret, camera_matrix, distortion_coefficients,
         rotation_vectors, translation_vectors,
         stdDeviationsIntrinsics, stdDeviationsExtrinsics,
         perViewErrors) = cv2.aruco.calibrateCameraCharucoExtended(
            charucoCorners=allCorners,
            charucoIds=allIds,
            board=self.board,
            imageSize=imsize,
            cameraMatrix=cameraMatrixInit,
            distCoeffs=distCoeffsInit,
            flags=flags,
            criteria=(cv2.TERM_CRITERIA_EPS & cv2.TERM_CRITERIA_COUNT, 50000, 1e-9))
        if self.traceLevel == 3 or self.traceLevel == 10:
            print('Per View Errors...')
            print(perViewErrors)
        return ret, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors

    def calibrate_fisheye(self, allCorners, allIds, imsize, hfov):
        one_pts = self.board.chessboardCorners
        obj_points = []
        for i in range(len(allIds)):
            obj_pts_sub = []
            for j in range(len(allIds[i])):
                obj_pts_sub.append(one_pts[allIds[i][j]])
            obj_points.append(np.array(obj_pts_sub, dtype=np.float32))

        cameraMatrixInit = np.array([[907.84859625,   0.0        , 995.15888273],
                                      [  0.0       ,  889.29269629, 627.49748034],
                                      [  0.0       ,    0.0       ,    1.0      ]])

 
        print("Camera Matrix initialization.............")
        print(cameraMatrixInit)
        flags = 0
        flags |= cv2.fisheye.CALIB_CHECK_COND 
        flags |= cv2.fisheye.CALIB_USE_INTRINSIC_GUESS 
        flags |= cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC 
        #flags |= cv2.fisheye.CALIB_FIX_SKEW
        distCoeffsInit = np.zeros((4, 1))
        term_criteria = (cv2.TERM_CRITERIA_COUNT +
                         cv2.TERM_CRITERIA_EPS, 50000, 1e-9)

        return cv2.fisheye.calibrate(obj_points, allCorners, imsize, cameraMatrixInit, distCoeffsInit, flags=flags, criteria=term_criteria)

    def calibrate_stereo(self, allCorners_l, allIds_l, allCorners_r, allIds_r, imsize, cameraMatrix_l, distCoeff_l, cameraMatrix_r, distCoeff_r, t_in, r_in):
        left_corners_sampled = []
        right_corners_sampled = []
        obj_pts = []
        one_pts = self.board.chessboardCorners
        
        if self.traceLevel == 2 or self.traceLevel == 4 or self.traceLevel == 10:
            print('Length of allIds_l')
            print(len(allIds_l))
            print('Length of allIds_r')
            print(len(allIds_r))

        for i in range(len(allIds_l)):
            left_sub_corners = []
            right_sub_corners = []
            obj_pts_sub = []
            #if len(allIds_l[i]) < 70 or len(allIds_r[i]) < 70:
            #      continue
            for j in range(len(allIds_l[i])):
                idx = np.where(allIds_r[i] == allIds_l[i][j])
                if idx[0].size == 0:
                    continue
                left_sub_corners.append(allCorners_l[i][j])
                right_sub_corners.append(allCorners_r[i][idx])
                obj_pts_sub.append(one_pts[allIds_l[i][j]])
            if len(left_sub_corners) > 3 and len(right_sub_corners) > 3:
                obj_pts.append(np.array(obj_pts_sub, dtype=np.float32))
                left_corners_sampled.append(
                    np.array(left_sub_corners, dtype=np.float32))
                right_corners_sampled.append(
                    np.array(right_sub_corners, dtype=np.float32))
            else:
                return -1, "Stereo Calib failed due to less common features"

        stereocalib_criteria = (cv2.TERM_CRITERIA_COUNT +
                                cv2.TERM_CRITERIA_EPS, 1000, 1e-9)

        if self.cameraModel == 'perspective':
            flags = 0
            # flags |= cv2.CALIB_USE_EXTRINSIC_GUESS
            # print(flags)

            flags |= cv2.CALIB_FIX_INTRINSIC
            # flags |= cv2.CALIB_USE_INTRINSIC_GUESS
            flags |= cv2.CALIB_RATIONAL_MODEL
            # print(flags)
            if self.traceLevel == 3 or self.traceLevel == 10:
                print('Printing Extrinsics guesses...')
                print(r_in)
                print(t_in)
            ret, M1, d1, M2, d2, R, T, E, F, _ = cv2.stereoCalibrateExtended(
                obj_pts, left_corners_sampled, right_corners_sampled,
                cameraMatrix_l, distCoeff_l, cameraMatrix_r, distCoeff_r, imsize,
                R=r_in, T=t_in, criteria=stereocalib_criteria , flags=flags)

            r_euler = Rotation.from_matrix(R).as_euler('xyz', degrees=True)
            print(f'Reprojection error is {ret}')
            if self.traceLevel == 3 or self.traceLevel == 10:
                print('Printing Extrinsics res...')
                print(R)
                print(T)
                print(f'Euler angles in XYZ {r_euler} degs')


            R_l, R_r, P_l, P_r, Q, validPixROI1, validPixROI2 = cv2.stereoRectify(
                cameraMatrix_l,
                distCoeff_l,
                cameraMatrix_r,
                distCoeff_r,
                imsize, R, T) # , alpha=0.1
            # self.P_l = P_l
            # self.P_r = P_r
            r_euler = Rotation.from_matrix(R_l).as_euler('xyz', degrees=True)
            if self.traceLevel == 5 or self.traceLevel == 10:
                print(f'R_L Euler angles in XYZ {r_euler}')
            r_euler = Rotation.from_matrix(R_r).as_euler('xyz', degrees=True)
            if self.traceLevel == 5 or self.traceLevel == 10:
                print(f'R_R Euler angles in XYZ {r_euler}')
            
            # print(f'P_l is \n {P_l}')
            # print(f'P_r is \n {P_r}')

            return [ret, R, T, R_l, R_r, P_l, P_r]
        elif self.cameraModel == 'fisheye':
            # make sure all images have the same *number of* points
            min_num_points = min([len(pts) for pts in obj_pts])
            obj_pts_truncated = [pts[:min_num_points] for pts in obj_pts]
            left_corners_truncated = [pts[:min_num_points] for pts in left_corners_sampled]
            right_corners_truncated = [pts[:min_num_points] for pts in right_corners_sampled]

            flags = 0
            # flags |= cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC
            # flags |= cv2.fisheye.CALIB_CHECK_COND
            # flags |= cv2.fisheye.CALIB_FIX_SKEW
            flags |= cv2.fisheye.CALIB_FIX_INTRINSIC
            flags |= cv2.fisheye.CALIB_FIX_K1
            flags |= cv2.fisheye.CALIB_FIX_K2
            flags |= cv2.fisheye.CALIB_FIX_K3 
            flags |= cv2.fisheye.CALIB_FIX_K4
            # flags |= cv2.CALIB_RATIONAL_MODEL
            # TODO(sACHIN): Try without intrinsic guess
            # flags |= cv2.CALIB_USE_INTRINSIC_GUESS
            # TODO(sACHIN): Try without intrinsic guess
            # flags |= cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC
            # flags = cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC + cv2.fisheye.CALIB_CHECK_COND + cv2.fisheye.CALIB_FIX_SKEW
            if self.traceLevel == 3 or self.traceLevel == 10:
                print('Fisyeye stereo model..................')
            (ret, M1, d1, M2, d2, R, T), E, F = cv2.fisheye.stereoCalibrate(
                obj_pts_truncated, left_corners_truncated, right_corners_truncated,
                cameraMatrix_l, distCoeff_l, cameraMatrix_r, distCoeff_r, imsize,
                flags=flags, criteria=stereocalib_criteria), None, None
            r_euler = Rotation.from_matrix(R).as_euler('xyz', degrees=True)
            print(f'Reprojection error is {ret}')
            if self.traceLevel == 3 or self.traceLevel == 10:
                print('Printing Extrinsics res...')
                print(R)
                print(T)
                print(f'Euler angles in XYZ {r_euler} degs')
            isHorizontal = np.absolute(T[0]) > np.absolute(T[1])
            
            if 0:
                if not isHorizontal:
                    rotated_k_l = cameraMatrix_l.copy()
                    rotated_k_r = cameraMatrix_r.copy()
                    rotated_k_l[0][0] = cameraMatrix_l[1][1] # swap fx and fy
                    rotated_k_r[0][0] = cameraMatrix_r[1][1] # swap fx and fy
                    rotated_k_l[1][1] = cameraMatrix_l[0][0] # swap fx and fy
                    rotated_k_r[1][1] = cameraMatrix_r[0][0] # swap fx and fy

                    rotated_k_l[0][2] = cameraMatrix_l[1][2] # swap optical center x and y
                    rotated_k_r[0][2] = cameraMatrix_r[1][2] # swap optical center x and y
                    rotated_k_l[1][2] = cameraMatrix_l[0][2] # swap optical center x and y
                    rotated_k_r[1][2] = cameraMatrix_r[0][2] # swap optical center x and y
                    
                    T_mod = T.copy()
                    T_mod[0] = T[1]
                    T_mod[1] = T[0]
                    
                    r = Rotation.from_euler('xyz', [r_euler[1], r_euler[0], r_euler[2]], degrees=True)
                    R_mod = r.as_matrix()
                    print(f' Image size is {imsize} and modified iamge size is {imsize[::-1]}')
                    R_l, R_r, P_l, P_r, Q = cv2.fisheye.stereoRectify(
                        rotated_k_l,
                        distCoeff_l,
                        rotated_k_r,
                        distCoeff_r,
                        imsize[::-1], R_mod, T_mod, flags=0)
                    # TODO revier things back to original style for Rotation and translation
                    r_euler = Rotation.from_matrix(R_l).as_euler('xyz', degrees=True)
                    R_l = Rotation.from_euler('xyz', [r_euler[1], r_euler[0], r_euler[2]], degrees=True).as_matrix()

                    r_euler = Rotation.from_matrix(R_r).as_euler('xyz', degrees=True)
                    R_r = Rotation.from_euler('xyz', [r_euler[1], r_euler[0], r_euler[2]], degrees=True).as_matrix()

                    temp = P_l[0][0]
                    P_l[0][0] = P_l[1][1]
                    P_l[1][1] = temp
                    temp = P_r[0][0]
                    P_r[0][0] = P_r[1][1]
                    P_r[1][1] = temp
                    
                    temp = P_l[0][2]
                    P_l[0][2] = P_l[1][2]
                    P_l[1][2] = temp
                    temp = P_r[0][2]
                    P_r[0][2] = P_r[1][2]
                    P_r[1][2] = temp
                    
                    temp = P_l[0][3]
                    P_l[0][3] = P_l[1][3]
                    P_l[1][3] = temp
                    temp = P_r[0][3]
                    P_r[0][3] = P_r[1][3]
                    P_r[1][3] = temp
                else:
                    R_l, R_r, P_l, P_r, Q = cv2.fisheye.stereoRectify(
                        cameraMatrix_l,
                        distCoeff_l,
                        cameraMatrix_r,
                        distCoeff_r,
                        imsize, R, T, flags=0)
            R_l, R_r, P_l, P_r, Q, validPixROI1, validPixROI2 = cv2.stereoRectify(
                cameraMatrix_l,
                distCoeff_l,
                cameraMatrix_r,
                distCoeff_r,
                imsize, R, T) # , alpha=0.1
            
            r_euler = Rotation.from_matrix(R_l).as_euler('xyz', degrees=True)
            if self.traceLevel == 3 or self.traceLevel == 10:
                print(f'R_L Euler angles in XYZ {r_euler}')
            r_euler = Rotation.from_matrix(R_r).as_euler('xyz', degrees=True)
            if self.traceLevel == 3 or self.traceLevel == 10:
                print(f'R_R Euler angles in XYZ {r_euler}')            
            
            return [ret, R, T, R_l, R_r, P_l, P_r]

    def display_rectification(self, image_data_pairs, images_corners_l, images_corners_r, image_epipolar_color, isHorizontal):
        print(
            "Displaying Stereo Pair for visual inspection. Press the [ESC] key to exit.")
        for idx, image_data_pair in enumerate(image_data_pairs):
            if isHorizontal:
                img_concat = cv2.hconcat(
                    [image_data_pair[0], image_data_pair[1]])
                for left_pt, right_pt, colorMode in zip(images_corners_l[idx], images_corners_r[idx], image_epipolar_color[idx]):
                    cv2.line(img_concat,
                             (int(left_pt[0][0]), int(left_pt[0][1])), (int(right_pt[0][0]) + image_data_pair[0].shape[1], int(right_pt[0][1])),
                             colors[colorMode], 1)
            else:
                img_concat = cv2.vconcat(
                    [image_data_pair[0], image_data_pair[1]])
                for left_pt, right_pt, colorMode in zip(images_corners_l[idx], images_corners_r[idx], image_epipolar_color[idx]):
                    cv2.line(img_concat,
                             (int(left_pt[0][0]), int(left_pt[0][1])), (int(right_pt[0][0]), int(right_pt[0][1])  + image_data_pair[0].shape[0]),
                             colors[colorMode], 1)

            img_concat = cv2.resize(
                img_concat, (0, 0), fx=0.8, fy=0.8)

            # show image
            cv2.imshow('Stereo Pair', img_concat)
            k = cv2.waitKey(0)
            if k == 27:  # Esc key to stop
                break

                # os._exit(0)
                # raise SystemExit()

        cv2.destroyWindow('Stereo Pair')

    def scale_image(self, img, scaled_res):
        expected_height = img.shape[0]*(scaled_res[1]/img.shape[1])
        if self.traceLevel == 2 or self.traceLevel == 10:
            print("Expected Height: {}".format(expected_height))

        if not (img.shape[0] == scaled_res[0] and img.shape[1] == scaled_res[1]):
            if int(expected_height) == scaled_res[0]:
                # resizing to have both stereo and rgb to have same
                # resolution to capture extrinsics of the rgb-right camera
                img = cv2.resize(img, (scaled_res[1], scaled_res[0]),
                                 interpolation=cv2.INTER_CUBIC)
                return img
            else:
                # resizing and cropping to have both stereo and rgb to have same resolution
                # to calculate extrinsics of the rgb-right camera
                scale_width = scaled_res[1]/img.shape[1]
                dest_res = (
                    int(img.shape[1] * scale_width), int(img.shape[0] * scale_width))
                img = cv2.resize(
                    img, dest_res, interpolation=cv2.INTER_CUBIC)
                if img.shape[0] < scaled_res[0]:
                    raise RuntimeError("resizeed height of rgb is smaller than required. {0} < {1}".format(
                        img.shape[0], scaled_res[0]))
                # print(gray.shape[0] - req_resolution[0])
                del_height = (img.shape[0] - scaled_res[0]) // 2
                # gray = gray[: req_resolution[0], :]
                img = img[del_height: del_height + scaled_res[0], :]
                return img
        else:
            return img
    
    def sgdEpipolar(self, images_left, images_right, M_lp, d_l, M_rp, d_r, r_l, r_r, kScaledL, kScaledR, scaled_res, isHorizontal):
        if self.cameraModel == 'perspective':
            mapx_l, mapy_l = cv2.initUndistortRectifyMap(
                M_lp, d_l, r_l, kScaledL, scaled_res[::-1], cv2.CV_32FC1)
            mapx_r, mapy_r = cv2.initUndistortRectifyMap(
                M_rp, d_r, r_r, kScaledR, scaled_res[::-1], cv2.CV_32FC1)
        else:
            mapx_l, mapy_l = cv2.fisheye.initUndistortRectifyMap(
                M_lp, d_l, r_l, kScaledL, scaled_res[::-1], cv2.CV_32FC1)
            mapx_r, mapy_r = cv2.fisheye.initUndistortRectifyMap(
                M_rp, d_r, r_r, kScaledR, scaled_res[::-1], cv2.CV_32FC1)

        
        image_data_pairs = []
        imagesCount = 0

        for image_left, image_right in zip(images_left, images_right):
            # read images
            imagesCount += 1
            # print(imagesCount)
            img_l = cv2.imread(image_left, 0)
            img_r = cv2.imread(image_right, 0)

            img_l = self.scale_image(img_l, scaled_res)
            img_r = self.scale_image(img_r, scaled_res)

            # warp right image
            # img_l = cv2.warpPerspective(img_l, self.H1, img_l.shape[::-1],
            #                             cv2.INTER_CUBIC +
            #                             cv2.WARP_FILL_OUTLIERS +
            #                             cv2.WARP_INVERSE_MAP)

            # img_r = cv2.warpPerspective(img_r, self.H2, img_r.shape[::-1],
            #                             cv2.INTER_CUBIC +
            #                             cv2.WARP_FILL_OUTLIERS +
            #                             cv2.WARP_INVERSE_MAP)

            img_l = cv2.remap(img_l, mapx_l, mapy_l, cv2.INTER_LINEAR)
            img_r = cv2.remap(img_r, mapx_r, mapy_r, cv2.INTER_LINEAR)

            image_data_pairs.append((img_l, img_r))

        imgpoints_r = []
        imgpoints_l = []
        criteria = (cv2.TERM_CRITERIA_EPS +
                    cv2.TERM_CRITERIA_MAX_ITER, 10000, 0.00001)
            
        for i, image_data_pair in enumerate(image_data_pairs):
            res2_l = self.detect_charuco_board(image_data_pair[0])
            res2_r = self.detect_charuco_board(image_data_pair[1])

            if res2_l[1] is not None and res2_r[2] is not None and len(res2_l[1]) > 3 and len(res2_r[1]) > 3:

                cv2.cornerSubPix(image_data_pair[0], res2_l[1], 
                                 winSize=(5, 5),
                                 zeroZone=(-1, -1),
                                 criteria=criteria)
                cv2.cornerSubPix(image_data_pair[1], res2_r[1],
                                 winSize=(5, 5),
                                 zeroZone=(-1, -1),
                                 criteria=criteria)

                # termination criteria
                img_pth_right = Path(images_right[i])
                img_pth_left = Path(images_left[i])
                org = (100, 50)
                # cv2.imshow('ltext', lText)
                # cv2.waitKey(0)
                localError = 0
                corners_l = []
                corners_r = []
                for j in range(len(res2_l[2])):
                    idx = np.where(res2_r[2] == res2_l[2][j])
                    if idx[0].size == 0:
                        continue
                    corners_l.append(res2_l[1][j])
                    corners_r.append(res2_r[1][idx])

                imgpoints_l.extend(corners_l)
                imgpoints_r.extend(corners_r)
                epi_error_sum = 0
                for l_pt, r_pt in zip(corners_l, corners_r):
                    if isHorizontal:
                        epi_error_sum += abs(l_pt[0][1] - r_pt[0][1])
                    else:
                        epi_error_sum += abs(l_pt[0][0] - r_pt[0][0])
                # localError = epi_error_sum / len(corners_l)

                # print("Average Epipolar in test Error per image on host in " + img_pth_right.name + " : " +
                #       str(localError))
            else:
                print('Numer of corners is in left -> {} and right -> {}'.format(
                    len(marker_corners_l), len(marker_corners_r)))
                raise SystemExit(1)

        epi_error_sum = 0
        for l_pt, r_pt in zip(imgpoints_l, imgpoints_r):
            if isHorizontal:
                epi_error_sum += abs(l_pt[0][1] - r_pt[0][1])
            else:
                epi_error_sum += abs(l_pt[0][0] - r_pt[0][0])

        avg_epipolar = epi_error_sum / len(imgpoints_r)
        print("Average Epipolar Error in test is : " + str(avg_epipolar))
        return avg_epipolar


    def test_epipolar_charuco(self, left_img_pth, right_img_pth, M_l, d_l, M_r, d_r, t, r_l, r_r, p_l, p_r):
        images_left = glob.glob(left_img_pth + '/*.png')
        images_right = glob.glob(right_img_pth + '/*.png')
        images_left.sort()
        images_right.sort()
        assert len(images_left) != 0, "ERROR: Images not read correctly"
        assert len(images_right) != 0, "ERROR: Images not read correctly"
        isHorizontal = np.absolute(t[0]) > np.absolute(t[1])

        scale = None
        scale_req = False
        frame_left_shape = cv2.imread(images_left[0], 0).shape
        frame_right_shape = cv2.imread(images_right[0], 0).shape
        scalable_res = frame_left_shape
        scaled_res = frame_right_shape
        if frame_right_shape[0] < frame_left_shape[0] and frame_right_shape[1] < frame_left_shape[1]:
            scale_req = True
            scale = frame_right_shape[1] / frame_left_shape[1]
        elif frame_right_shape[0] > frame_left_shape[0] and frame_right_shape[1] > frame_left_shape[1]:
            scale_req = True
            scale = frame_left_shape[1] / frame_right_shape[1]
            scalable_res = frame_right_shape
            scaled_res = frame_left_shape

        if scale_req:
            scaled_height = scale * scalable_res[0]
            diff = scaled_height - scaled_res[0]
            if diff < 0:
                scaled_res = (int(scaled_height), scaled_res[1])
        if self.traceLevel == 3 or self.traceLevel == 10:
            print(
                f'Is scale Req: {scale_req}\n scale value: {scale} \n scalable Res: {scalable_res} \n scale Res: {scaled_res}')
            print("Original res Left :{}".format(frame_left_shape))
            print("Original res Right :{}".format(frame_right_shape))
        # print("Scale res :{}".format(scaled_res))

        M_lp = self.scale_intrinsics(M_l, frame_left_shape, scaled_res)
        M_rp = self.scale_intrinsics(M_r, frame_right_shape, scaled_res)
        if rectProjectionMode:
            p_lp = self.scale_intrinsics(p_l, frame_left_shape, scaled_res)
            p_rp = self.scale_intrinsics(p_r, frame_right_shape, scaled_res)
            print('Projection intrinsics ....')
            print(p_lp)
            print(p_rp)

        criteria = (cv2.TERM_CRITERIA_EPS +
                    cv2.TERM_CRITERIA_MAX_ITER, 10000, 0.00001)

        # TODO(Sachin): Observe Images by adding visualization 
        # TODO(Sachin): Check if the stetch is only in calibration Images
        print('Original intrinsics ....')
        print(f"L {M_lp}")
        print(f"R: {M_rp}")
        if self.traceLevel == 3 or self.traceLevel == 10:
            print(f'Width and height is {scaled_res[::-1]}')
        # kScaledL, _ = cv2.getOptimalNewCameraMatrix(M_r, d_r, scaled_res[::-1], 0)
        # kScaledL, _ = cv2.getOptimalNewCameraMatrix(M_r, d_l, scaled_res[::-1], 0)
        # kScaledR, _ = cv2.getOptimalNewCameraMatrix(M_r, d_r, scaled_res[::-1], 0)
        kScaledR = kScaledL = M_rp

        if self.cameraModel != 'perspective':
            kScaledR = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(M_r, d_r, scaled_res[::-1], np.eye(3), fov_scale=1.1)
            kScaledL = kScaledR

        if rectProjectionMode:
            kScaledL = p_lp
            kScaledR = p_rp
            
        print('Intrinsics from the getOptimalNewCameraMatrix/Original ....')
        print(f"L: {kScaledL}")
        print(f"R: {kScaledR}")
        oldEpipolarError = None
        epQueue = deque()
        movePos = True
        if 0:
            while True:
                
                epError = self.sgdEpipolar(images_left, images_right, M_lp, d_l, M_rp, d_r, r_l, r_r, kScaledL, kScaledR, scaled_res, isHorizontal)

                if oldEpipolarError is None:
                    epQueue.append((epError, kScaledR))
                    oldEpipolarError = epError
                    kScaledR[0][0] += 1
                    kScaledR[1][1] += 1
                    continue
                if movePos:
                    if epError < oldEpipolarError:
                        epQueue.append((epError, kScaledR))
                        oldEpipolarError = epError
                        kScaledR[0][0] += 1
                        kScaledR[1][1] += 1
                    else:
                        movePos = False
                        startPos = epQueue.popleft()
                        oldEpipolarError = startPos[0]
                        kScaledR = startPos[1]
                        epQueue.appendleft((oldEpipolarError, kScaledR))
                        kScaledR[0][0] -= 1
                        kScaledR[1][1] -= 1
                else:
                    if epError < oldEpipolarError:
                        epQueue.appendleft((epError, kScaledR))
                        oldEpipolarError = epError
                        kScaledR[0][0] -= 1
                        kScaledR[1][1] -= 1
                    else:
                        break
            oldEpipolarError = None
            while epQueue:
                currEp, currK = epQueue.popleft()
                if oldEpipolarError is None:
                    oldEpipolarError = currEp
                    kScaledR = currK
                else:
                    currEp, currK = epQueue.popleft()
                    if currEp < oldEpipolarError:
                        oldEpipolarError = currEp
                        kScaledR = currK


        #print('Lets find the best epipolar Error')



        if self.cameraModel == 'perspective':
            mapx_l, mapy_l = cv2.initUndistortRectifyMap(
                M_lp, d_l, r_l, kScaledL, scaled_res[::-1], cv2.CV_32FC1)
            mapx_r, mapy_r = cv2.initUndistortRectifyMap(
                M_rp, d_r, r_r, kScaledR, scaled_res[::-1], cv2.CV_32FC1)
        else:
            mapx_l, mapy_l = cv2.fisheye.initUndistortRectifyMap(
                M_lp, d_l, r_l, kScaledL, scaled_res[::-1], cv2.CV_32FC1)
            mapx_r, mapy_r = cv2.fisheye.initUndistortRectifyMap(
                M_rp, d_r, r_r, kScaledR, scaled_res[::-1], cv2.CV_32FC1)

        image_data_pairs = []
        for image_left, image_right in zip(images_left, images_right):
            # read images
            img_l = cv2.imread(image_left, 0)
            img_r = cv2.imread(image_right, 0)

            img_l = self.scale_image(img_l, scaled_res)
            img_r = self.scale_image(img_r, scaled_res)
            # print(img_l.shape)
            # print(img_r.shape)

            # warp right image
            # img_l = cv2.warpPerspective(img_l, self.H1, img_l.shape[::-1],
            #                             cv2.INTER_CUBIC +
            #                             cv2.WARP_FILL_OUTLIERS +
            #                             cv2.WARP_INVERSE_MAP)

            # img_r = cv2.warpPerspective(img_r, self.H2, img_r.shape[::-1],
            #                             cv2.INTER_CUBIC +
            #                             cv2.WARP_FILL_OUTLIERS +
            #                             cv2.WARP_INVERSE_MAP)

            img_l = cv2.remap(img_l, mapx_l, mapy_l, cv2.INTER_LINEAR)
            img_r = cv2.remap(img_r, mapx_r, mapy_r, cv2.INTER_LINEAR)

            image_data_pairs.append((img_l, img_r))
            
            if self.traceLevel == 4 or self.traceLevel == 5 or self.traceLevel == 10:
                cv2.imshow("undistorted-Left", img_l)
                cv2.imshow("undistorted-right", img_r)
                # print(f'image path - {im}')
                # print(f'Image Undistorted Size {undistorted_img.shape}')
                k = cv2.waitKey(0)
                if k == 27:  # Esc key to stop
                    break
        if self.traceLevel == 4 or self.traceLevel == 5 or self.traceLevel == 10:
          cv2.destroyWindow("undistorted-Left")
          cv2.destroyWindow("undistorted-right")  
        # compute metrics
        imgpoints_r = []
        imgpoints_l = []
        image_epipolar_color = []
        # new_imagePairs = [])
        for i, image_data_pair in enumerate(image_data_pairs):
            res2_l = self.detect_charuco_board(image_data_pair[0])
            res2_r = self.detect_charuco_board(image_data_pair[1])
            
            if self.traceLevel == 2 or self.traceLevel == 4 or self.traceLevel == 10:
                print(f'Marekrs length for pair {i} is: L {len(res2_l[1])} | R {len(res2_r[1])} ')

            img_concat = cv2.hconcat([image_data_pair[0], image_data_pair[1]])
            img_concat = cv2.cvtColor(img_concat, cv2.COLOR_GRAY2RGB)
            line_row = 0
            while line_row < img_concat.shape[0]:
                cv2.line(img_concat,
                         (0, line_row), (img_concat.shape[1], line_row),
                         (0, 255, 0), 1)
                line_row += 30

            # cv2.imshow('Stereo Pair', img_concat)
            # k = cv2.waitKey(0)
            # if k == 27:  # Esc key to stop
            #     break

            if res2_l[1] is not None and res2_r[2] is not None and len(res2_l[1]) > 3 and len(res2_r[1]) > 3:

                cv2.cornerSubPix(image_data_pair[0], res2_l[1],
                                 winSize=(5, 5),
                                 zeroZone=(-1, -1),
                                 criteria=criteria)
                cv2.cornerSubPix(image_data_pair[1], res2_r[1],
                                 winSize=(5, 5),
                                 zeroZone=(-1, -1),
                                 criteria=criteria)

                # termination criteria
                img_pth_right = Path(images_right[i])
                img_pth_left = Path(images_left[i])
                org = (100, 50)
                # cv2.imshow('ltext', lText)
                # cv2.waitKey(0)
                localError = 0
                corners_l = []
                corners_r = []
                for j in range(len(res2_l[2])):
                    idx = np.where(res2_r[2] == res2_l[2][j])
                    if idx[0].size == 0:
                        continue
                    corners_l.append(res2_l[1][j])
                    corners_r.append(res2_r[1][idx])

                imgpoints_l.append(corners_l)
                imgpoints_r.append(corners_r)
                epi_error_sum = 0
                corner_epipolar_color = []
                for l_pt, r_pt in zip(corners_l, corners_r):
                    if isHorizontal:
                        curr_epipolar_error = abs(l_pt[0][1] - r_pt[0][1])
                    else:
                        curr_epipolar_error = abs(l_pt[0][0] - r_pt[0][0])
                    if curr_epipolar_error >= 1:
                        corner_epipolar_color.append(1)
                    else:
                        corner_epipolar_color.append(0)
                    epi_error_sum += curr_epipolar_error
                localError = epi_error_sum / len(corners_l)
                image_epipolar_color.append(corner_epipolar_color)
                if self.traceLevel == 2 or self.traceLevel == 3 or self.traceLevel == 4 or self.traceLevel == 10:
                    print("Epipolar Error per image on host in " + img_pth_right.name + " : " +
                        str(localError))
            else:
                print('Numer of corners is in left -> {} and right -> {}'.format(
                    len(marker_corners_l), len(marker_corners_r)))
                return -1
            lText = cv2.putText(cv2.cvtColor(image_data_pair[0],cv2.COLOR_GRAY2RGB), img_pth_left.name, org, cv2.FONT_HERSHEY_SIMPLEX, 1, (2, 0, 255), 2, cv2.LINE_AA)
            rText = cv2.putText(cv2.cvtColor(image_data_pair[1],cv2.COLOR_GRAY2RGB), img_pth_right.name + " Error: " + str(localError), org, cv2.FONT_HERSHEY_SIMPLEX, 1, (2, 0, 255), 2, cv2.LINE_AA)
            image_data_pairs[i] = (lText, rText)


        epi_error_sum = 0
        total_corners = 0
        for corners_l, corners_r in zip(imgpoints_l, imgpoints_r):
            total_corners += len(corners_l)
            for l_pt, r_pt in zip(corners_l, corners_r):
                if isHorizontal:
                    epi_error_sum += abs(l_pt[0][1] - r_pt[0][1])
                else:
                    epi_error_sum += abs(l_pt[0][0] - r_pt[0][0])

        avg_epipolar = epi_error_sum / total_corners
        print("Average Epipolar Error is : " + str(avg_epipolar))

        if self.enable_rectification_disp:
            self.display_rectification(image_data_pairs, imgpoints_l, imgpoints_r, image_epipolar_color, isHorizontal)

        return avg_epipolar

    def create_save_mesh(self):  # , output_path):

        curr_path = Path(__file__).parent.resolve()
        print("Mesh path")
        print(curr_path)

        if self.cameraModel == "perspective":
            map_x_l, map_y_l = cv2.initUndistortRectifyMap(
                self.M1, self.d1, self.R1, self.M2, self.img_shape, cv2.CV_32FC1)
            map_x_r, map_y_r = cv2.initUndistortRectifyMap(
                self.M2, self.d2, self.R2, self.M2, self.img_shape, cv2.CV_32FC1)
        else:    
            map_x_l, map_y_l = cv2.fisheye.initUndistortRectifyMap(
                self.M1, self.d1, self.R1, self.M2, self.img_shape, cv2.CV_32FC1)
            map_x_r, map_y_r = cv2.fisheye.initUndistortRectifyMap(
                self.M2, self.d2, self.R2, self.M2, self.img_shape, cv2.CV_32FC1)

        """ 
        map_x_l_fp32 = map_x_l.astype(np.float32)
        map_y_l_fp32 = map_y_l.astype(np.float32)
        map_x_r_fp32 = map_x_r.astype(np.float32)
        map_y_r_fp32 = map_y_r.astype(np.float32)
        
                
        print("shape of maps")
        print(map_x_l.shape)
        print(map_y_l.shape)
        print(map_x_r.shape)
        print(map_y_r.shape) """

        meshCellSize = 16
        mesh_left = []
        mesh_right = []

        for y in range(map_x_l.shape[0] + 1):
            if y % meshCellSize == 0:
                row_left = []
                row_right = []
                for x in range(map_x_l.shape[1] + 1):
                    if x % meshCellSize == 0:
                        if y == map_x_l.shape[0] and x == map_x_l.shape[1]:
                            row_left.append(map_y_l[y - 1, x - 1])
                            row_left.append(map_x_l[y - 1, x - 1])
                            row_right.append(map_y_r[y - 1, x - 1])
                            row_right.append(map_x_r[y - 1, x - 1])
                        elif y == map_x_l.shape[0]:
                            row_left.append(map_y_l[y - 1, x])
                            row_left.append(map_x_l[y - 1, x])
                            row_right.append(map_y_r[y - 1, x])
                            row_right.append(map_x_r[y - 1, x])
                        elif x == map_x_l.shape[1]:
                            row_left.append(map_y_l[y, x - 1])
                            row_left.append(map_x_l[y, x - 1])
                            row_right.append(map_y_r[y, x - 1])
                            row_right.append(map_x_r[y, x - 1])
                        else:
                            row_left.append(map_y_l[y, x])
                            row_left.append(map_x_l[y, x])
                            row_right.append(map_y_r[y, x])
                            row_right.append(map_x_r[y, x])
                if (map_x_l.shape[1] % meshCellSize) % 2 != 0:
                    row_left.append(0)
                    row_left.append(0)
                    row_right.append(0)
                    row_right.append(0)

                mesh_left.append(row_left)
                mesh_right.append(row_right)

        mesh_left = np.array(mesh_left)
        mesh_right = np.array(mesh_right)
        left_mesh_fpath = str(curr_path) + '/../resources/left_mesh.calib'
        right_mesh_fpath = str(curr_path) + '/../resources/right_mesh.calib'
        mesh_left.tofile(left_mesh_fpath)
        mesh_right.tofile(right_mesh_fpath)