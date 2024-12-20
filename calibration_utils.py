from scipy.spatial.transform import Rotation
from .worker import parallel_function
from typing import List, Tuple
from itertools import chain
from .types import *
import numpy as np
import time
import cv2

PER_CCM = True
EXTRINSICS_PER_CCM = False
colors = [(0, 255 , 0), (0, 0, 255)]

class StereoExceptions(Exception):
  def __init__(self, message, stage, path=None, *args, **kwargs) -> None:
    self.stage = stage
    self.path = path
    super().__init__(message, *args)

  @property
  def summary(self) -> str:
    """
    Returns a more comprehensive summary of the exception
    """
    return f"'{self.args[0]}' (occured during stage '{self.stage}')"

@parallel_function
def estimate_pose_and_filter(camData: CameraData, corners, ids, charucoBoard: CharucoBoard):
  """Very rough corner filtering on a single image"""

  objpoints = charucoBoard.board.chessboardCorners[ids]

  ini_threshold=5
  threshold = None

  objects = []
  all_objects = []
  while len(objects) < len(objpoints[:,0,0]) * camData['min_inliers']:
    if ini_threshold > camData['max_threshold']:
      break
    ret, rvec, tvec, objects  = cv2.solvePnPRansac(objpoints, corners, camData['intrinsics'], camData['dist_coeff'], flags = cv2.SOLVEPNP_P3P, reprojectionError = ini_threshold,  iterationsCount = 10000, confidence = 0.9)

    if not ret:
      raise RuntimeError('Exception') # TODO : Handle

    all_objects.append(objects)
    imgpoints2 = objpoints.copy()

    all_corners2 = corners.copy()
    all_corners2 = np.array([all_corners2[id[0]] for id in objects])
    imgpoints2 = np.array([imgpoints2[id[0]] for id in objects])

    ret, rvec, tvec = cv2.solvePnP(imgpoints2, all_corners2, camData['intrinsics'], camData['dist_coeff'])

    ini_threshold += camData['threshold_stepper']

  if ids.size > 0 and corners.size > 0: # TODO : Try to remove the np reshaping
    ids = ids.flatten()  # Flatten the IDs from 2D to 1D
    imgpoints2, _ = cv2.projectPoints(objpoints, rvec, tvec, camData['intrinsics'], camData['dist_coeff'])
    corners2 = corners.reshape(-1, 2)
    imgpoints2 = imgpoints2.reshape(-1, 2)

    errors = np.linalg.norm(corners2 - imgpoints2, axis=1)
    if threshold == None:
      threshold = max(2*np.median(errors), 150)
    valid_mask = errors <= threshold
    removed_mask = ~valid_mask

    valid_ids = ids[valid_mask]

    return corners2[valid_mask].reshape(-1, 1, 2), valid_ids.reshape(-1, 1).astype(np.int32), corners2[removed_mask]

@parallel_function
def detect_charuco_board(image: np.array, board: CharucoBoard):
  arucoParams = cv2.aruco.DetectorParameters_create()
  arucoParams.minMarkerDistanceRate = 0.01
  markers, marker_ids, rejectedImgPoints = cv2.aruco.detectMarkers(image, board.dictionary, parameters=arucoParams)  # First, detect markers
  marker_corners, marker_ids, refusd, recoverd = cv2.aruco.refineDetectedMarkers(image, board.board, markers, marker_ids, rejectedCorners=rejectedImgPoints)
  criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10000, 0.00001)

  # If found, add object points, image points (after refining them)
  if len(marker_corners)>0:
    num_corners, corners, ids = cv2.aruco.interpolateCornersCharuco(marker_corners,marker_ids, image, board.board, minMarkers = 1)
    if corners is not None and ids is not None and len(corners) > 3:
      corners = cv2.cornerSubPix(image, corners,
                              winSize=(5, 5),
                              zeroZone=(-1, -1),
                              criteria=criteria)
    return corners, ids
  else:
    raise RuntimeError('Failed to detect corners on image')

@parallel_function
def get_features(config: CalibrationConfig, camData: CameraData) -> CameraData:
  f = camData['size'][0] / (2 * np.tan(np.deg2rad(camData["hfov"]/2)))

  camData['intrinsics'] = np.array([
     [f,     0.0,    camData['size'][0]/2],
     [0.0,   f,      camData['size'][1]/2],
     [0.0,   0.0,    1.0]
  ])
  camData['dist_coeff'] = np.zeros((12, 1))

   # check if there are any suspicious corners with high reprojection error
  max_threshold = 75 + config.initialMaxThreshold * (camData['hfov']/ 30 + camData['size'][1] / 800 * 0.2)
  threshold_stepper = int(1.5 * (camData['hfov'] / 30 + camData['size'][1] / 800))
  if threshold_stepper < 1:
    threshold_stepper = 1
  min_inliers = 1 - config.initialMinFiltered * (camData['hfov'] / 60 + camData['size'][1] / 800 * 0.2)
  camData['max_threshold'] = max_threshold
  camData['threshold_stepper'] = threshold_stepper
  camData['min_inliers'] = min_inliers

  return camData

@parallel_function
def calibrate_charuco(camData: CameraData, allCorners, allIds, dataset: Dataset):
  # TODO : If we still need this check it needs to be elsewhere
  # if sum([len(corners) < 4 for corners in filteredCorners]) > 0.15 * len(filteredCorners):
  #   raise RuntimeError(f"More than 1/4 of images has less than 4 corners for {cam_info['name']}")

  distortion_flags = get_distortion_flags(camData['distortion_model'])
  flags = cv2.CALIB_USE_INTRINSIC_GUESS + distortion_flags
  
  # Convert to int32 from uint32 # TODO : Shouldn't be necessary
  for i, ids in enumerate(allIds): allIds[i] = ids.reshape(-1, 1).astype(np.int32)

  # Filter for only images with >6 corners # TODO : Shouldn't be in here, should be in a separate necessary filtering function
  allCorners2 = []
  allIds2 = []

  for corners, ids in zip(allCorners, allIds):
    if len(ids) < 6:
      continue
    allCorners2.append(corners)
    allIds2.append(ids)

  #try:
  (ret, camera_matrix, distortion_coefficients,
       rotation_vectors, translation_vectors,
       stdDeviationsIntrinsics, stdDeviationsExtrinsics,
       perViewErrors) = cv2.aruco.calibrateCameraCharucoExtended(
        charucoCorners=allCorners2,
        charucoIds=allIds2,
        board=dataset.board.board,
        imageSize=camData['size'],
        cameraMatrix=camData['intrinsics'],
        distCoeffs=camData['dist_coeff'],
        flags=flags,
        criteria=(cv2.TERM_CRITERIA_EPS & cv2.TERM_CRITERIA_COUNT, 1000, 1e-6))
  #except:
  #  return f"First intrisic calibration failed for {cam_info['size']}", None, None

  camData['intrinsics'] = camera_matrix
  camData['dist_coeff'] = distortion_coefficients
  camData['filtered_corners'] = allCorners2
  camData['filtered_ids'] = allIds2
  return camData

@parallel_function
def filter_features_fisheye(camData: CameraData, intrinsic_img, all_features, all_ids):
  f = camData['size'][0] / (2 * np.tan(np.deg2rad(camData["hfov"]/2)))
  print("INTRINSIC CALIBRATION")
  cameraIntrinsics = np.array([[f,  0.0,    camData['size'][0]/2],
                 [0.0,   f,    camData['size'][1]/2],
                 [0.0,   0.0,    1.0]])

  distCoeff = np.zeros((12, 1))

  if camData["name"] in intrinsic_img:
    raise RuntimeError('This is broken')
    all_features, all_ids, filtered_images = remove_features(filtered_features, filtered_ids, intrinsic_img[camData["name"]], image_files)
  else:
    filtered_images = camData['images_path']

  filtered_features = all_features
  filtered_ids = all_ids

  camData['filtered_ids'] = filtered_ids
  camData['filtered_corners'] = filtered_features
  camData['intrinsics'] = cameraIntrinsics
  camData['dist_coeff'] = distCoeff

  return camData

@parallel_function
def calibrate_ccm_intrinsics_per_ccm(config: CalibrationConfig, camData: CameraData, dataset: Dataset):
  start = time.time()
  print('starting calibrate_wf')
  ret, cameraIntrinsics, distCoeff, _, _, filtered_ids, filtered_corners, size, coverageImage, all_corners, all_ids = calibrate_wf_intrinsics(config, camData, dataset)
  if isinstance(ret, str) and all_ids is None:
    raise RuntimeError('Exception' + ret) # TODO : Handle
  print(f'calibrate_wf took {round(time.time() - start, 2)}s')

  camData['intrinsics'] = cameraIntrinsics
  camData['dist_coeff'] = distCoeff
  camData['reprojection_error'] = ret
  print("Reprojection error of {0}: {1}".format(
    camData['name'], ret))
  return camData, filtered_corners, filtered_ids

def calibrate_ccm_intrinsics(config: CalibrationConfig, camData: CameraData):
  imsize = camData['size']
  hfov = camData['hfov']
  name = camData['name']
  allCorners = camData['filtered_corners'] # TODO : I don't think this has a way to get here from one of the codepaths in matin in the else:
  allIds = camData['filtered_ids']
  calib_model = camData['calib_model']
  distortionModel = camData['distortion_model']
  
  coverageImage = np.ones(imsize[::-1], np.uint8) * 255
  coverageImage = cv2.cvtColor(coverageImage, cv2.COLOR_GRAY2BGR)
  coverageImage = draw_corners(allCorners, coverageImage)
  if calib_model == 'perspective':
    distortion_flags = get_distortion_flags(distortionModel)
    ret, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors, filtered_ids, filtered_corners, allCorners, allIds  = calibrate_camera_charuco(
      allCorners, allIds, imsize, distortion_flags, camData['intrinsics'], camData['dist_coeff'])
    # undistort_visualization(
    #   self, image_files, camera_matrix, distortion_coefficients, imsize, name)

    return ret, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors, filtered_ids, filtered_corners, imsize, coverageImage, allCorners, allIds
  else:
    print('Fisheye--------------------------------------------------')
    ret, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors, filtered_ids, filtered_corners = calibrate_fisheye(
      config, allCorners, allIds, imsize, hfov, name)
    # undistort_visualization(
    #   self, image_files, camera_matrix, distortion_coefficients, imsize, name)
    print('Fisheye rotation vector', rotation_vectors[0])
    print('Fisheye translation vector', translation_vectors[0])
  
  camData['filtered_ids'] = filtered_ids
  camData['filtered_corners'] = filtered_corners

  camData['intrinsics'] = camera_matrix
  camData['dist_coeff'] = distortion_coefficients
  camData['reprojection_error'] = ret
  print("Reprojection error of {0}: {1}".format(
    camData['name'], ret))

  return camData

@parallel_function
def calibrate_stereo_perspective(config: CalibrationConfig, obj_pts, allLeftCorners, allRightCorners, leftCamData: CameraData, rightCamData: CameraData):
  cameraMatrix_l, distCoeff_l, cameraMatrix_r, distCoeff_r, left_distortion_model = leftCamData['intrinsics'], leftCamData['dist_coeff'], rightCamData['intrinsics'], rightCamData['dist_coeff'], leftCamData['distortion_model']
  specTranslation = leftCamData['extrinsics']['specTranslation']
  rot = leftCamData['extrinsics']['rotation']

  t_in = np.array(
    [specTranslation['x'], specTranslation['y'], specTranslation['z']], dtype=np.float32)
  r_in = Rotation.from_euler(
    'xyz', [rot['r'], rot['p'], rot['y']], degrees=True).as_matrix().astype(np.float32)

  flags = 0
  # flags |= cv2.CALIB_USE_EXTRINSIC_GUESS
  # print(flags)
  flags = cv2.CALIB_FIX_INTRINSIC
  distortion_flags = get_distortion_flags(left_distortion_model)
  flags += distortion_flags
  # print(flags)
  ret, M1, d1, M2, d2, R, T, E, F, _ = cv2.stereoCalibrateExtended(
  obj_pts, allLeftCorners, allRightCorners,
  cameraMatrix_l, distCoeff_l, cameraMatrix_r, distCoeff_r, None,
  R=r_in, T=t_in, criteria=config.stereoCalibCriteria, flags=flags)

  r_euler = Rotation.from_matrix(R).as_euler('xyz', degrees=True)
  print(f'Epipolar error is {ret}')
  print('Printing Extrinsics res...')
  print(R)
  print(T)
  print(f'Euler angles in XYZ {r_euler} degs')

  R_l, R_r, P_l, P_r, Q, validPixROI1, validPixROI2 = cv2.stereoRectify(
    cameraMatrix_l,
    distCoeff_l,
    cameraMatrix_r,
    distCoeff_r,
    None, R, T) # , alpha=0.1
  # self.P_l = P_l
  # self.P_r = P_r
  r_euler = Rotation.from_matrix(R_l).as_euler('xyz', degrees=True)
  r_euler = Rotation.from_matrix(R_r).as_euler('xyz', degrees=True)

  return [ret, R, T, R_l, R_r, P_l, P_r]

@parallel_function
def calibrate_stereo_perspective_per_ccm(config: CalibrationConfig, obj_pts, left_corners_sampled, right_corners_sampled, left_cam_info, right_cam_info):
  cameraMatrix_l, distCoeff_l, cameraMatrix_r, distCoeff_r = left_cam_info['intrinsics'], left_cam_info['dist_coeff'], right_cam_info['intrinsics'], right_cam_info['dist_coeff']
  specTranslation = left_cam_info['extrinsics']['specTranslation']
  rot = left_cam_info['extrinsics']['rotation']

  t_in = np.array(
    [specTranslation['x'], specTranslation['y'], specTranslation['z']], dtype=np.float32)
  r_in = Rotation.from_euler(
    'xyz', [rot['r'], rot['p'], rot['y']], degrees=True).as_matrix().astype(np.float32)

  flags = cv2.CALIB_FIX_INTRINSIC
  ret, M1, d1, M2, d2, R, T, E, F, _ = cv2.stereoCalibrateExtended(
  obj_pts, left_corners_sampled, right_corners_sampled,
  np.eye(3), np.zeros(12), np.eye(3), np.zeros(12), None,
  R=r_in, T=t_in, criteria=config.stereoCalibCriteria , flags=flags)

  r_euler = Rotation.from_matrix(R).as_euler('xyz', degrees=True)
  scale = ((cameraMatrix_l[0][0]*cameraMatrix_r[0][0]))
  print(f'Epipolar error without scale: {ret}')
  print(f'Epipolar error with scale: {ret*np.sqrt(scale)}')
  print('Printing Extrinsics res...')
  print(R)
  print(T)
  print(f'Euler angles in XYZ {r_euler} degs')
  R_l, R_r, P_l, P_r, Q, validPixROI1, validPixROI2 = cv2.stereoRectify(
    cameraMatrix_l,
    distCoeff_l,
    cameraMatrix_r,
    distCoeff_r,
    None, R, T) # , alpha=0.1
  # self.P_l = P_l
  # self.P_r = P_r
  r_euler = Rotation.from_matrix(R_l).as_euler('xyz', degrees=True)
  r_euler = Rotation.from_matrix(R_r).as_euler('xyz', degrees=True)

  # print(f'P_l is \n {P_l}')
  # print(f'P_r is \n {P_r}')
  return [ret, R, T, R_l, R_r, P_l, P_r]

@parallel_function
def calibrate_stereo_fisheye(config: CalibrationConfig, obj_pts, left_corners_sampled, right_corners_sampled, left_cam_info, right_cam_info):
  cameraMatrix_l, distCoeff_l, cameraMatrix_r, distCoeff_r = left_cam_info['intrinsics'], left_cam_info['dist_coeff'], right_cam_info['intrinsics'], right_cam_info['dist_coeff']
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
  # flags |= cv2.fisheye.CALIB_FIX_K1
  # flags |= cv2.fisheye.CALIB_FIX_K2
  # flags |= cv2.fisheye.CALIB_FIX_K3
  # flags |= cv2.fisheye.CALIB_FIX_K4
  # flags |= cv2.CALIB_RATIONAL_MODEL
  # flags |= cv2.CALIB_USE_INTRINSIC_GUESS
  # flags |= cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC
  # flags = cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC + cv2.fisheye.CALIB_CHECK_COND + cv2.fisheye.CALIB_FIX_SKEW
  (ret, M1, d1, M2, d2, R, T), E, F = cv2.fisheye.stereoCalibrate(
    obj_pts_truncated, left_corners_truncated, right_corners_truncated,
    cameraMatrix_l, distCoeff_l, cameraMatrix_r, distCoeff_r, None,
    flags=flags, criteria=config.stereoCalibCriteria), None, None
  r_euler = Rotation.from_matrix(R).as_euler('xyz', degrees=True)
  print(f'Reprojection error is {ret}')
  isHorizontal = np.absolute(T[0]) > np.absolute(T[1])

  R_l, R_r, P_l, P_r, Q = cv2.fisheye.stereoRectify(
    cameraMatrix_l,
    distCoeff_l,
    cameraMatrix_r,
    distCoeff_r,
    None, R, T, flags=0)

  r_euler = Rotation.from_matrix(R_l).as_euler('xyz', degrees=True)
  r_euler = Rotation.from_matrix(R_r).as_euler('xyz', degrees=True)

  return [ret, R, T, R_l, R_r, P_l, P_r]

@parallel_function
def find_stereo_common_features(leftCorners, leftIds, rightCorners, rightIds, board: CharucoBoard):
  left_corners_sampled = []
  right_corners_sampled = []
  obj_pts = []
  failed = 0

  for i, _ in enumerate(leftIds): # For ids in all images
    commonIds = np.intersect1d(leftIds[i], rightIds[i])
    left_sub_corners = leftCorners[i][np.isin(leftIds[i], commonIds)]
    right_sub_corners = rightCorners[i][np.isin(rightIds[i], commonIds)]
    obj_pts_sub = board.board.chessboardCorners[commonIds]

    if len(commonIds) > 6:
      obj_pts.append(obj_pts_sub)
      left_corners_sampled.append(left_sub_corners)
      right_corners_sampled.append(right_sub_corners)
    else:
      failed += 1

  if failed > len(leftIds) / 3:
    raise RuntimeError('More than 1/3 of images had less than 6 common features found')

  return left_corners_sampled, right_corners_sampled, obj_pts

@parallel_function
def undistort_points_perspective(allCorners, camInfo):
  return [cv2.undistortPoints(np.array(corners), camInfo['intrinsics'], camInfo['dist_coeff'], P=camInfo['intrinsics']) for corners in allCorners]

@parallel_function
def undistort_points_fisheye(allCorners, camInfo):
  return [cv2.fisheye.undistortPoints(np.array(corners), camInfo['intrinsics'], camInfo['dist_coeff'], P=camInfo['intrinsics']) for corners in allCorners]

@parallel_function
def calculate_epipolar_error(left_cam_info: CameraData, right_cam_info: CameraData, left_cam: Dataset, right_cam: Dataset, board_config, extrinsics):
  if extrinsics[0] == -1:
    return -1, extrinsics[1]
  stereoConfig = None
  if 'stereo_config' in board_config:
    leftCamName = board_config['cameras'][board_config['stereo_config']['left_cam']]['name']
    rightCamName = board_config['cameras'][board_config['stereo_config']['right_cam']]['name']
    if leftCamName == left_cam.name and rightCamName == right_cam.name: # TODO : Is this supposed to take the last camera pair?
      stereoConfig = {
        'rectification_left': extrinsics[3],
        'rectification_right': extrinsics[4]
      }
    elif leftCamName == right_cam and rightCamName == left_cam:
      stereoConfig = {
        'rectification_left': extrinsics[4],
        'rectification_right': extrinsics[3]
      }

  print('<-------------Epipolar error of {} and {} ------------>'.format(
    left_cam_info['name'], right_cam_info['name']))
  #print(f"dist {left_cam_info['name']}: {left_cam_info['dist_coeff']}")
  #print(f"dist {right_cam_info['name']}: {right_cam_info['dist_coeff']}")
  if left_cam_info['intrinsics'][0][0] < right_cam_info['intrinsics'][0][0]:
    scale = right_cam_info['intrinsics'][0][0]
  else:
    scale = left_cam_info['intrinsics'][0][0]
  if PER_CCM and EXTRINSICS_PER_CCM:
    scale = ((left_cam_info['intrinsics'][0][0]*right_cam_info['intrinsics'][0][0] + left_cam_info['intrinsics'][1][1]*right_cam_info['intrinsics'][1][1])/2)
    print(f"Epipolar error {extrinsics[0]*np.sqrt(scale)}")
    left_cam_info['extrinsics']['epipolar_error'] = extrinsics[0]*np.sqrt(scale)
  else:
    print(f"Epipolar error {extrinsics[0]}")
    left_cam_info['extrinsics']['epipolar_error'] = extrinsics[0]

  left_cam_info['extrinsics']['rotation_matrix'] = extrinsics[1]
  left_cam_info['extrinsics']['translation'] = extrinsics[2]

  return left_cam_info, stereoConfig

def get_distortion_flags(distortionModel: DistortionModel):
  if distortionModel == None:
    print("Use DEFAULT model")
    flags = cv2.CALIB_RATIONAL_MODEL

  elif all(char in '01' for char in str(distortionModel)):
    flags = cv2.CALIB_RATIONAL_MODEL
    flags += cv2.CALIB_TILTED_MODEL
    flags += cv2.CALIB_THIN_PRISM_MODEL
    distFlags = int(distortionModel, 2)

    if distFlags & (1 << 0):
      print("FIX_K1")
      flags += cv2.CALIB_FIX_K1
    if distFlags & (1 << 1):
      print("FIX_K2")
      flags += cv2.CALIB_FIX_K2
    if distFlags & (1 << 2):
      print("FIX_K3")
      flags += cv2.CALIB_FIX_K3
    if distFlags & (1 << 3):
      print("FIX_K4")
      flags += cv2.CALIB_FIX_K4
    if distFlags & (1 << 4):
      print("FIX_K5")
      flags += cv2.CALIB_FIX_K5
    if distFlags & (1 << 5):
      print("FIX_K6")
      flags += cv2.CALIB_FIX_K6
    if distFlags & (1 << 6):
      print("FIX_TANGENT_DISTORTION")
      flags += cv2.CALIB_ZERO_TANGENT_DIST
    if distFlags & (1 << 7):
      print("FIX_TILTED_DISTORTION")
      flags += cv2.CALIB_FIX_TAUX_TAUY
    if distFlags & (1 << 8):
      print("FIX_PRISM_DISTORTION")
      flags += cv2.CALIB_FIX_S1_S2_S3_S4

  elif distortionModel == DistortionModel.Normal:
    print("Using NORMAL model")
    flags = cv2.CALIB_RATIONAL_MODEL
    flags += cv2.CALIB_TILTED_MODEL

  elif distortionModel == DistortionModel.Tilted:
    print("Using TILTED model")
    flags = cv2.CALIB_RATIONAL_MODEL
    flags += cv2.CALIB_TILTED_MODEL

  elif distortionModel == DistortionModel.Prism:
    print("Using PRISM model")
    flags = cv2.CALIB_RATIONAL_MODEL
    flags += cv2.CALIB_TILTED_MODEL
    flags += cv2.CALIB_THIN_PRISM_MODEL

  elif distortionModel == DistortionModel.Thermal:
    print("Using THERMAL model")
    flags = cv2.CALIB_RATIONAL_MODEL
    flags += cv2.CALIB_FIX_K3
    flags += cv2.CALIB_FIX_K5
    flags += cv2.CALIB_FIX_K6

  elif isinstance(distortionModel, int):
    print("Using CUSTOM flags")
    flags = distortionModel
  return flags

def calibrate_wf_intrinsics(config: CalibrationConfig, camData: CameraData, dataset: Dataset):
  name = camData['name']
  allCorners = camData['filtered_corners']
  allIds = camData['filtered_ids']
  imsize = camData['size']
  hfov = camData['hfov']
  calib_model = camData['calib_model']
  distortionModel = camData['distortion_model']
  cameraIntrinsics = camData['intrinsics']
  distCoeff = camData['dist_coeff']
  
  coverageImage = np.ones(imsize[::-1], np.uint8) * 255
  coverageImage = cv2.cvtColor(coverageImage, cv2.COLOR_GRAY2BGR)
  coverageImage = draw_corners(allCorners, coverageImage)
  if calib_model == 'perspective':
    distortion_flags = get_distortion_flags(distortionModel)
    ret, cameraIntrinsics, distCoeff, rotation_vectors, translation_vectors, filtered_ids, filtered_corners, allCorners, allIds  = calibrate_camera_charuco(
      allCorners, allIds, imsize, distortion_flags, cameraIntrinsics, distCoeff, dataset)

    return ret, cameraIntrinsics, distCoeff, rotation_vectors, translation_vectors, filtered_ids, filtered_corners, imsize, coverageImage, allCorners, allIds
  else:
    print('Fisheye--------------------------------------------------')
    ret, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors, filtered_ids, filtered_corners = calibrate_fisheye(
      config, allCorners, allIds, imsize, hfov, name)
    print('Fisheye rotation vector', rotation_vectors[0])
    print('Fisheye translation vector', translation_vectors[0])

    # (Height, width)
    return ret, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors, filtered_ids, filtered_corners, imsize, coverageImage, allCorners, allIds

def draw_corners(charuco_corners, displayframe):
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

def features_filtering_function(rvecs, tvecs, cameraMatrix, distCoeffs, filtered_corners,filtered_id, dataset: Dataset, threshold = None):
  whole_error = []
  all_points = []
  all_corners = []
  all_error = []
  all_ids = []
  removed_corners = []
  removed_points = []
  removed_ids = []
  removed_error = []
  display_corners = []
  display_points = []
  circle_size = 0
  reported_error = []
  for i, (corners, ids) in enumerate(zip(filtered_corners, filtered_id)):
    if ids is not None and corners.size > 0:
      ids = ids.flatten()  # Flatten the IDs from 2D to 1D
      objPoints = np.array([dataset.board.board.chessboardCorners[id] for id in ids], dtype=np.float32)
      imgpoints2, _ = cv2.projectPoints(objPoints, rvecs[i], tvecs[i], cameraMatrix, distCoeffs)
      corners2 = corners.reshape(-1, 2)
      imgpoints2 = imgpoints2.reshape(-1, 2)

      errors = np.linalg.norm(corners2 - imgpoints2, axis=1)
      if threshold == None:
        threshold = max(2*np.median(errors), 150)
      valid_mask = errors <= threshold
      removed_mask = ~valid_mask

      # Collect valid IDs in the original format (array of arrays)
      valid_ids = ids[valid_mask]
      all_ids.append(valid_ids.reshape(-1, 1).astype(np.int32))  # Reshape and store as array of arrays

      # Collect data for valid points
      reported_error.extend(errors)
      all_error.extend(errors[valid_mask])
      display_corners.extend(corners2)
      display_points.extend(imgpoints2[valid_mask])
      all_points.append(imgpoints2[valid_mask])  # Collect valid points for calibration
      all_corners.append(corners2[valid_mask].reshape(-1, 1, 2))   # Collect valid corners for calibration

      removed_corners.extend(corners2[removed_mask])
      removed_points.extend(imgpoints2[removed_mask])
      removed_ids.extend(ids[removed_mask])
      removed_error.extend(errors[removed_mask])

      total_error_squared = np.sum(errors[valid_mask]**2)
      total_points = len(objPoints[valid_mask])
      rms_error = np.sqrt(total_error_squared / total_points if total_points else 0)
      whole_error.append(rms_error)

    total_error_squared = 0
    total_points = 0

  return all_corners ,all_ids, all_error, removed_corners, removed_ids, removed_error

def camera_pose_charuco(objpoints: np.array, corners: np.array, ids: np.array, K: np.array, d: np.array, ini_threshold = 2, min_inliers = 0.95, threshold_stepper = 1, max_threshold = 50):
  objects = []
  while len(objects) < len(objpoints[:,0,0]) * min_inliers:
    if ini_threshold > max_threshold:
      break
    ret, rvec, tvec, objects  = cv2.solvePnPRansac(objpoints, corners, K, d, flags = cv2.SOLVEPNP_P3P, reprojectionError = ini_threshold,  iterationsCount = 10000, confidence = 0.9)

    if not ret:
      break

    imgpoints2 = objpoints.copy()

    all_corners = corners.copy()
    all_corners = np.array([all_corners[id[0]] for id in objects])
    imgpoints2 = np.array([imgpoints2[id[0]] for id in objects])

    ret, rvec, tvec = cv2.solvePnP(imgpoints2, all_corners, K, d)
    imgpoints2, _ = cv2.projectPoints(imgpoints2, rvec, tvec, K, d)

    ini_threshold += threshold_stepper
  if ret:
    return rvec, tvec
  else:
    raise RuntimeError() # TODO : Handle

def compute_reprojection_errors(obj_pts: np.array, img_pts: np.array, K: np.array, dist: np.array, rvec: np.array, tvec: np.array, fisheye = False):
  if fisheye:
    proj_pts, _ = cv2.fisheye.projectPoints(obj_pts, rvec, tvec, K, dist)
  else:
    proj_pts, _ = cv2.projectPoints(obj_pts, rvec, tvec, K, dist)
  errs = np.linalg.norm(np.squeeze(proj_pts) - np.squeeze(img_pts), axis = 1)
  return errs

def undistort_visualization(self, img_list, K, D, img_size, name):
  for index, im in enumerate(img_list):
    # print(im)
    img = cv2.imread(im)
    # h, w = img.shape[:2]
    if self._cameraModel == 'perspective':
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

    if index == 0:
      undistorted_file_path = self.data_path + '/' + name + f'_undistorted.png'
      cv2.imwrite(undistorted_file_path, undistorted_img)

def filter_corner_outliers(config: CalibrationConfig, allIds, allCorners, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors):
  corners_removed = False
  for i in range(len(allIds)):
    corners = allCorners[i]
    ids = allIds[i]
    objpts = config.board.chessboardCorners[ids]
    if config.cameraModel == "fisheye":
      errs = compute_reprojection_errors(objpts, corners, camera_matrix, distortion_coefficients, rotation_vectors[i], translation_vectors[i], fisheye = True)
    else:
      errs = compute_reprojection_errors(objpts, corners, camera_matrix, distortion_coefficients, rotation_vectors[i], translation_vectors[i])
    suspicious_err_thr = max(2*np.median(errs), 100)
    n_offending_pts = np.sum(errs > suspicious_err_thr)
    offending_pts_idxs = np.where(errs > suspicious_err_thr)[0]
    # check if there are offending points and if they form a minority
    if n_offending_pts > 0 and n_offending_pts < len(corners)/5:
      print(f"removing {n_offending_pts} offending points with errs {errs[offending_pts_idxs]}")
      corners_removed = True
      #remove the offending points
      offset = 0
      allCorners[i] = np.delete(allCorners[i],offending_pts_idxs, axis = 0)
      allIds[i] = np.delete(allIds[i],offending_pts_idxs, axis = 0)
  return corners_removed, allIds, allCorners

def calibrate_camera_charuco(allCorners, allIds, imsize, distortion_flags, cameraIntrinsics, distCoeff, dataset: Dataset):
  """
  Calibrates the camera using the dected corners.
  """
  threshold = 2 * imsize[1]/800.0
   # check if there are any suspicious corners with high reprojection error
  rvecs = []
  tvecs = []
  index = 0
  for corners, ids in zip(allCorners, allIds):
    objpts = dataset.board.board.chessboardCorners[ids]
    rvec, tvec = camera_pose_charuco(objpts, corners, ids, cameraIntrinsics, distCoeff)
    tvecs.append(tvec)
    rvecs.append(rvec)
    index += 1

  # Here we need to get initialK and parameters for each camera ready and fill them inside reconstructed reprojection error per point
  ret = 0.0
  flags = cv2.CALIB_USE_INTRINSIC_GUESS
  flags += distortion_flags

  #   flags = (cv2.CALIB_RATIONAL_MODEL)
  reprojection = []
  num_threshold = []
  iterations_array = []
  intrinsic_array = {"f_x": [], "f_y": [], "c_x": [],"c_y": []}
  distortion_array = {}
  index = 0
  camera_matrix = cameraIntrinsics
  distortion_coefficients = distCoeff
  rotation_vectors = rvecs
  translation_vectors = tvecs
  translation_array_x = []
  translation_array_y = []
  translation_array_z = []
  import time
  if True:
    whole = time.time()
    while True:
      intrinsic_array['f_x'].append(camera_matrix[0][0])
      intrinsic_array['f_y'].append(camera_matrix[1][1])
      intrinsic_array['c_x'].append(camera_matrix[0][2])
      intrinsic_array['c_y'].append(camera_matrix[1][2])
      num_threshold.append(threshold)

      translation_array_x.append(np.mean(np.array(translation_vectors).T[0][0]))
      translation_array_y.append(np.mean(np.array(translation_vectors).T[0][1]))
      translation_array_z.append(np.mean(np.array(translation_vectors).T[0][2]))

      start = time.time()
      filtered_corners, filtered_ids, all_error, removed_corners, removed_ids, removed_error = features_filtering_function(rotation_vectors, translation_vectors, camera_matrix, distortion_coefficients, allCorners, allIds, threshold = threshold, dataset=dataset)
      iterations_array.append(index)
      reprojection.append(ret)
      for i in range(len(distortion_coefficients)):
        if i not in distortion_array:
          distortion_array[i] = []
        distortion_array[i].append(distortion_coefficients[i][0])
      print(f"Each filtering {time.time() - start}")
      start = time.time()
      try:
        (ret, camera_matrix, distortion_coefficients,
         rotation_vectors, translation_vectors,
         stdDeviationsIntrinsics, stdDeviationsExtrinsics,
         perViewErrors) = cv2.aruco.calibrateCameraCharucoExtended(
          charucoCorners=filtered_corners,
          charucoIds=filtered_ids,
          board=dataset.board.board,
          imageSize=imsize,
          cameraMatrix=cameraIntrinsics,
          distCoeffs=distCoeff,
          flags=flags,
          criteria=(cv2.TERM_CRITERIA_EPS & cv2.TERM_CRITERIA_COUNT, 50000, 1e-9))
      except:
        print('failed on dataset', dataset.name)
        raise StereoExceptions(message="Intrisic calibration failed", stage="intrinsic_calibration", element='', id=0)
      cameraIntrinsics = camera_matrix
      distCoeff = distortion_coefficients
      threshold = 5 * imsize[1]/800.0
      print(f"Each calibration {time.time()-start}")
      index += 1
      if  index > 5: #or (previous_ids == removed_ids and len(previous_ids) >= len(removed_ids) and index > 2):
        print(f"Whole procedure: {time.time() - whole}")
        break
      #previous_ids = removed_ids
  return ret, camera_matrix, distortion_coefficients, rotation_vectors, translation_vectors, filtered_ids, filtered_corners, allCorners, allIds

@parallel_function
def calibrate_fisheye(config: CalibrationConfig, allCorners, allIds, imsize, hfov, name):
  f_init = imsize[0]/np.deg2rad(hfov)*1.15

  cameraMatrixInit = np.array([[f_init, 0.      , imsize[0]/2],
                 [0.      , f_init, imsize[1]/2],
                 [0.      , 0.      , 1.      ]])
  distCoeffsInit = np.zeros((4,1))
   # check if there are any suspicious corners with high reprojection error
  rvecs = []
  tvecs = []
  for corners, ids in zip(allCorners, allIds):
    objpts = config.board.chessboardCorners[ids]
    corners_undist = cv2.fisheye.undistortPoints(corners, cameraMatrixInit, distCoeffsInit)
    rvec, tvec = camera_pose_charuco(objpts, corners_undist,ids, np.eye(3), np.array((0.0,0,0,0)))
    tvecs.append(tvec)
    rvecs.append(rvec)

  corners_removed, filtered_ids, filtered_corners = filter_corner_outliers(config, allIds, allCorners, cameraMatrixInit, distCoeffsInit, rvecs, tvecs)

  obj_points = []
  for ids in filtered_ids:
    obj_points.append(config.board.chessboardCorners[ids])
  # TODO :Maybe this can be obj_points = config.board.chessboardCorners[filtered_ids]

  print("Camera Matrix initialization.............")
  print(cameraMatrixInit)
  flags = 0
  flags |= cv2.fisheye.CALIB_CHECK_COND
  flags |= cv2.fisheye.CALIB_USE_INTRINSIC_GUESS
  flags |= cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC
  flags |= cv2.fisheye.CALIB_FIX_SKEW

  term_criteria = (cv2.TERM_CRITERIA_COUNT +
           cv2.TERM_CRITERIA_EPS, 30, 1e-9)
  try:
    res, K, d, rvecs, tvecs =  cv2.fisheye.calibrate(obj_points, filtered_corners, None, cameraMatrixInit, distCoeffsInit, flags=flags, criteria=term_criteria)
  except:
    # calibration failed for full FOV, let's try to limit the corners to smaller part of FOV first to find initial parameters
    success = False
    crop = 0.95
    while not success:
      print(f"trying crop factor {crop}")
      obj_points_limited = []
      corners_limited = []
      for obj_pts, corners in zip(obj_points, filtered_corners):
        obj_points_tmp = []
        corners_tmp = []
        for obj_pt, corner in zip(obj_pts, corners):
          check_x = corner[0,0] > imsize[0]*(1-crop) and corner[0,0] < imsize[0]*crop
          check_y = corner[0,1] > imsize[1]*(1-crop) and corner[0,1] < imsize[1]*crop
          if check_x and check_y:
            obj_points_tmp.append(obj_pt)
            corners_tmp.append(corner)
        obj_points_limited.append(np.array(obj_points_tmp))
        corners_limited.append(np.array(corners_tmp))
      try:
        res, K, d, rvecs, tvecs = cv2.fisheye.calibrate(obj_points_limited, corners_limited, None, cameraMatrixInit, distCoeffsInit, flags=flags, criteria=term_criteria)
        print(f"success with crop factor {crop}")
        success = True
        break
      except:
        print(f"failed with crop factor {crop}")
        if crop > 0.7:
          crop -= 0.05
        else:
          raise Exception("Calibration failed: Tried maximum crop factor and still no success")
    if success:
      # trying the full FOV once more with better initial K
      print(f"new K init {K}")
      print(f"new d_init {d}")
      try:
        res, K, d, rvecs, tvecs =  cv2.fisheye.calibrate(obj_points, filtered_corners, imsize, K, distCoeffsInit, flags=flags, criteria=term_criteria)
      except:
        print(f"Failed the full res calib, using calibration with crop factor {crop}")

  return res, K, d, rvecs, tvecs, filtered_ids, filtered_corners

@parallel_function
def calibrate_camera(config, board_config, camera_model, intrinsicCameras: List[Dataset] = [], extrinsicPairs: List[Tuple[Dataset, Dataset]] = []):
  camInfos = {}
  stereoConfigs = []
  allExtrinsics = []
  filteredCharucos = {}

  # Compile a list of unique datasets
  uniqueDatasets = intrinsicCameras
  for dataset in chain(*extrinsicPairs):
    if dataset not in uniqueDatasets:
      uniqueDatasets.append(dataset)

  # Calibrate camera intrinsics for all provided datasets
  for dataset in uniqueDatasets:
    camData: CameraData = [c for c in board_config['cameras'].values() if c['name'] == dataset.name][0]

    if "calib_model" in camData and len(camData["calib_model"].split("_")) > 1:
      cameraModel_ccm, model_ccm = camData["calib_model"].split("_")
      if cameraModel_ccm == "fisheye":
        model_ccm == None
      calib_model = cameraModel_ccm
      distortion_model = model_ccm
    else:
      calib_model = camera_model
      distortion_model = DistortionModel.Tilted # Use the tilted model by default

    camData['size'] = dataset.imageSize
    camData['calib_model'] = calib_model
    camData['distortion_model'] = distortion_model

    if len(dataset.allCorners) and len(dataset.allIds):
      allCorners, allIds = dataset.allCorners, dataset.allIds
    elif len(dataset.images):
      #allCorners, allIds = detect_charuco_board(list(dataset.images), dataset.board)
      
      allCorners, allids = [], []
      for image in list(dataset.images):
        corners, ids = detect_charuco_board(image, dataset.board)
        allCorners.append(corners)
        allIds.append(ids)
    else:
      raise RuntimeError(f'Dataset \'{dataset.name}\' doesn\'t contain corners or images')

    if PER_CCM:
      camData = get_features(config, camData)
      if camera_model== "fisheye":
        camData = filter_features_fisheye(camData, allCorners, allIds) # TODO : Input types are wrong
      elif dataset.enableFiltering:
        filteredCorners, filteredIds = [], []
        for corners, ids in zip(allCorners, allIds):
          corners, ids, _ = estimate_pose_and_filter(camData, corners, ids, dataset.board)
          filteredCorners.append(corners)
          filteredIds.append(ids)
        corners, ids = filteredCorners, filteredIds

        #corners, ids = estimate_pose_and_filter(camData, allCorners, allIds, dataset.board)
      else:
        corners, ids = allCorners, allIds

      camData = calibrate_charuco(camData, corners, ids, dataset)
      camData, corners, ids = calibrate_ccm_intrinsics_per_ccm(config, camData, dataset)
      camInfos[dataset.id] = camData
      filteredCharucos[dataset.id] = [corners, ids]
    else:
      camData = calibrate_ccm_intrinsics(config, camData) # TODO : Not a parallel function

  for left, right in extrinsicPairs:
    left_cam_info = camInfos[left.id]
    right_cam_info = camInfos[right.id]
    leftCorners, leftIds = filteredCharucos[left.id]
    rightCorners, rightIds = filteredCharucos[right.id]

    left_corners_sampled, right_corners_sampled, obj_pts= find_stereo_common_features(leftCorners, leftIds, rightCorners, rightIds, left.board)

    if PER_CCM and EXTRINSICS_PER_CCM:
      if left_cam_info['calib_model'] == "perspective":
        left_corners_sampled = undistort_points_perspective(left_corners_sampled, left_cam_info)
        right_corners_sampled = undistort_points_perspective(right_corners_sampled, right_cam_info)
      else:
        left_corners_sampled = undistort_points_fisheye(left_corners_sampled, left_cam_info)
        right_corners_sampled = undistort_points_fisheye(right_corners_sampled, right_cam_info)

      extrinsics = calibrate_stereo_perspective_per_ccm(config, obj_pts, left_corners_sampled, right_corners_sampled, left_cam_info, right_cam_info)
    else:
      if camera_model == 'perspective':
        extrinsics = calibrate_stereo_perspective(config, obj_pts, left_corners_sampled, right_corners_sampled, left_cam_info, right_cam_info)
      elif camera_model == 'fisheye':
        extrinsics = calibrate_stereo_fisheye(config, obj_pts, left_corners_sampled, right_corners_sampled, left_cam_info, right_cam_info)
    left_cam_info, stereo_config = calculate_epipolar_error(left_cam_info, right_cam_info, left, right, board_config, extrinsics)
    allExtrinsics.append(extrinsics)
    camInfos[left.id] = left_cam_info
    stereoConfigs.append(stereo_config)

  return board_config, filteredCharucos, allExtrinsics, camInfos, stereoConfigs

class StereoCalibration(object):
  """Class to Calculate Calibration and Rectify a Stereo Camera."""

  def calibrate(self, board_config, camera_model, intrinsicCameras: List[Dataset] = [], extrinsicPairs: List[Tuple[Dataset, Dataset]] = [], initial_max_threshold = 15, initial_min_filtered = 0.05, debug: bool = False):
    """Function to calculate calibration for stereo camera."""
    config = CalibrationConfig(
      initial_max_threshold, initial_min_filtered,
      camera_model, (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 300, 1e-9)
    )

    for a, b in extrinsicPairs:
      if len(a.allIds) != len(b.allIds):
        print('Not all dataset for extrinsic calibration have the same number of images')
        raise RuntimeError('Not all dataset for extrinsic calibration have the same number of images') # TODO : This isn't thorough enough
      if a.board is not b.board:
        print('Extrinsic pair has different dataset board')
        raise RuntimeError('Extrinsic pair has different dataset board')

    #board_config, filteredCharucos, allExtrinsics, camInfos, stereoConfigs = calibrate_camera.run_parallel(10, config, board_config, camera_model, intrinsicCameras, extrinsicPairs)
    board_config, filteredCharucos, allExtrinsics, camInfos, stereoConfigs = calibrate_camera.run_parallel(10, config, board_config, camera_model, intrinsicCameras, extrinsicPairs)

    # Construct board config from calibrated cam infos
    for dataset in intrinsicCameras:
      for socket in board_config['cameras']:
        if board_config['cameras'][socket]['name'] == dataset.name:
          #board_config['cameras'][socket] = camInfos[dataset.id].ret()
          board_config['cameras'][socket] = camInfos[dataset.id]

    for left, _ in extrinsicPairs:
      for socket in board_config['cameras']:
        if board_config['cameras'][socket]['name'] == left.name:
          #board_config['cameras'][socket]['extrinsics'] = camInfos[left.id].ret()['extrinsics']
          board_config['cameras'][socket]['extrinsics'] = camInfos[left.id]['extrinsics']

    for stereoConfig in stereoConfigs:
      #if stereoConfig.ret():
      #  board_config['stereo_config'].update(stereoConfig.ret())
      if stereoConfig:
        board_config['stereo_config'].update(stereoConfig)

    #for key in filteredCharucos.keys():
      #filteredCharucos[key] = [e.ret() for e in filteredCharucos[key]]

    if debug:
      #return [s.ret() for s in stereoConfigs], [e.ret() for e in allExtrinsics], board_config, {k: v.ret() for k, v in camInfos.items()}, filteredCharucos
      return stereoConfigs, allExtrinsics, board_config, camInfos, filteredCharucos

    return board_config, filteredCharucos