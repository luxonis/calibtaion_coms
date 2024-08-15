import logging
logging.getLogger('matplotlib').setLevel(logging.WARNING)

from scipy.spatial.transform import Rotation
from typing import TypedDict, List, Tuple
from .worker import ParallelWorker
import matplotlib.pyplot as plt
import cv2.aruco as aruco
from pathlib import Path
import numpy as np
import time
import glob
import cv2


plt.rcParams.update({'font.size': 16})

PER_CCM = True
EXTRINSICS_PER_CCM = False

class ProxyDict:
  def __init__(self, squaresX = 16, squaresY = 9, squareSize = 1.0, markerSize = 0.8, dictSize = cv2.aruco.DICT_4X4_1000):
    self.squaresX = squaresX
    self.squaresY = squaresY
    self.squareSize = squareSize
    self.markerSize = markerSize
    self.dictSize = dictSize

  def __getstate__(self):
    state = self.__dict__.copy()
    for hidden in ['_board', '_dictionary']:
      if hidden in state:
        del state[hidden]
    return state

  def __build(self):
    self._dictionary = aruco.Dictionary_get(self.dictSize)
    self._board = aruco.CharucoBoard_create(self.squaresX, self.squaresY, self.squareSize, self.markerSize, self._dictionary)

  @property
  def dictionary(self):
    if not hasattr(self, '_dictionary'):
      self.__build()
    return self._dictionary

  @property
  def board(self):
    if not hasattr(self, '_board'):
      self.__build()
    return self._board

class CalibrationConfig:
  def __init__(self, proxyDict = ProxyDict(), enableFiltering = True, ccmModel = '', disableCameras = [], initialMaxThreshold = 0, initialMinFiltered = 0, calibrationMaxThreshold = 0, calibrationMinFiltered = 0,
               cameraModel = 0, stereoCalibCriteria = 0, features = None):
    self._proxyDict = proxyDict
    self.enableFiltering = enableFiltering
    self.ccmModel = ccmModel # Distortion model
    self.disableCameras = disableCameras
    self.initialMaxThreshold = initialMaxThreshold
    self.initialMinFiltered = initialMinFiltered
    self.calibrationMaxThreshold = calibrationMaxThreshold
    self.calibrationMinFiltered = calibrationMinFiltered
    self.cameraModel = cameraModel
    self.stereoCalibCriteria = stereoCalibCriteria
    self.features = features # None | 'checker_board' | 'charuco'

  @property
  def dictionary(self):
    return self._proxyDict.dictionary

  @property
  def board(self):
    return self._proxyDict.board

class CameraData(TypedDict):
  calib_model: str
  dist_coeff: str
  distortion_model: str
  extrinsics: str
  to_cam: str
  filtered_corners: str
  type: str
  filtered_ids: str
  height: str
  width: str
  hfov: str
  socket: str
  images_path: str
  imsize: str
  intrinsics: str
  sensorName: str
  hasAutofocus: str
  model: str
  max_threshold: str
  min_inliers: str
  name: str
  reprojection_error: str
  size: str
  threshold_stepper: str
  features: str
  ids: str

colors = [(0, 255 , 0), (0, 0, 255)]

class StereoExceptions(Exception):
  def __init__(self, message, stage, path=None, *args, **kwargs) -> None:
    self.stage = stage
    self.path = path
    super().__init__(message, *args, **kwargs)

  @property
  def summary(self) -> str:
    """
    Returns a more comprehensive summary of the exception
    """
    return f"'{self.args[0]}' (occured during stage '{self.stage}')"

def estimate_pose_and_filter_single(config: CalibrationConfig, camData: CameraData, corners, ids):
  objpoints = config.board.chessboardCorners[ids]

  ini_threshold=5
  threshold = None

  objects = []
  all_objects = []
  while len(objects) < len(objpoints[:,0,0]) * camData['min_inliers']:
    if ini_threshold > camData['max_threshold']:
      break
    if camData['name'] == 'thermal': # TODO : It doesn't make sense to rerun this loop if Ransac is dsabled
      imgpoints2 = objpoints.copy()

      all_corners2 = corners.copy()
    else:
      ret, rvec, tvec, objects  = cv2.solvePnPRansac(objpoints, corners, camData['intrinsics'], camData['dist_coeff'], flags = cv2.SOLVEPNP_P3P, reprojectionError = ini_threshold,  iterationsCount = 10000, confidence = 0.9)
      all_objects.append(objects)
      imgpoints2 = objpoints.copy()

      all_corners2 = corners.copy()
      all_corners2 = np.array([all_corners2[id[0]] for id in objects])
      imgpoints2 = np.array([imgpoints2[id[0]] for id in objects])

    ret, rvec, tvec = cv2.solvePnP(imgpoints2, all_corners2, camData['intrinsics'], camData['dist_coeff'])

    ini_threshold += camData['threshold_stepper']
  if not ret:
    raise RuntimeError('Exception') # TODO : Handle

  if ids is not None and corners.size > 0: # TODO : Try to remove the np reshaping
    ids = ids.flatten()  # Flatten the IDs from 2D to 1D
    imgpoints2, _ = cv2.projectPoints(objpoints, rvec, tvec, camData['intrinsics'], camData['dist_coeff'])
    corners2 = corners.reshape(-1, 2)
    imgpoints2 = imgpoints2.reshape(-1, 2)

    errors = np.linalg.norm(corners2 - imgpoints2, axis=1)
    if threshold == None:
      threshold = max(2*np.median(errors), 150)
    valid_mask = errors <= threshold
    removed_mask = ~valid_mask

    # Collect valid IDs in the original format (array of arrays)
    valid_ids = ids[valid_mask]
    #filtered_ids.append(valid_ids.reshape(-1, 1).astype(np.int32))  # Reshape and store as array of arrays

    # Collect data for valid points
    #filtered_corners.append(corners2[valid_mask].reshape(-1, 1, 2))   # Collect valid corners for calibration

    #removed_corners.extend(corners2[removed_mask])
    return corners2[valid_mask].reshape(-1, 1, 2), valid_ids.reshape(-1, 1).astype(np.int32), corners2[removed_mask]

def get_features(config: CalibrationConfig, camData: CameraData, charucos) -> Tuple[CameraData, list, list]:
  #all_features, all_ids, imsize = getting_features(config, camData['images_path'], camData['width'], camData['height'], charucos=charucos)
  
  allCorners = []
  allIds = []
  for ids, charuco in charucos:
    allCorners.append(charuco)
    allIds.append(ids)

#   if isinstance(all_features, str) and all_ids is None:
#     raise RuntimeError(f'Exception {all_features}') # TODO : Handle
  f = camData['imsize'][0] / (2 * np.tan(np.deg2rad(camData["hfov"]/2)))

  camData['intrinsics'] = np.array([
     [f,     0.0,    camData['width']/2],
     [0.0,   f,      camData['height']/2],
     [0.0,   0.0,    1.0]
  ])
  camData['dist_coeff'] = np.zeros((12, 1))

   # check if there are any suspicious corners with high reprojection error
  max_threshold = 75 + config.initialMaxThreshold * (camData['hfov']/ 30 + camData['imsize'][1] / 800 * 0.2)
  threshold_stepper = int(1.5 * (camData['hfov'] / 30 + camData['imsize'][1] / 800))
  if threshold_stepper < 1:
    threshold_stepper = 1
  min_inliers = 1 - config.initialMinFiltered * (camData['hfov'] / 60 + camData['imsize'][1] / 800 * 0.2)
  camData['max_threshold'] = max_threshold
  camData['threshold_stepper'] = threshold_stepper
  camData['min_inliers'] = min_inliers

  return camData, allCorners, allIds

def estimate_pose_and_filter(config: CalibrationConfig, camData: CameraData, allCornersAndIds):
  filtered_corners = []
  filtered_ids = []
  for corners, ids in allCornersAndIds:
    corners, ids, _ = estimate_pose_and_filter_single(config, camData, corners, ids)
    filtered_corners.append(corners)
    filtered_ids.append(ids)

  return filtered_corners, filtered_ids

def calibrate_charuco(config: CalibrationConfig, camData: CameraData, corners, ids):
  # TODO : If we still need this check it needs to be elsewhere
  # if sum([len(corners) < 4 for corners in filteredCorners]) > 0.15 * len(filteredCorners):
  #   raise RuntimeError(f"More than 1/4 of images has less than 4 corners for {cam_info['name']}")

  distortion_flags = get_distortion_flags(camData['distortion_model'])
  flags = cv2.CALIB_USE_INTRINSIC_GUESS + distortion_flags

  #try:
  (ret, camera_matrix, distortion_coefficients,
       rotation_vectors, translation_vectors,
       stdDeviationsIntrinsics, stdDeviationsExtrinsics,
       perViewErrors) = cv2.aruco.calibrateCameraCharucoExtended(
        charucoCorners=corners,
        charucoIds=ids,
        board=config.board,
        imageSize=camData['imsize'],
        cameraMatrix=camData['intrinsics'],
        distCoeffs=camData['dist_coeff'],
        flags=flags,
        criteria=(cv2.TERM_CRITERIA_EPS & cv2.TERM_CRITERIA_COUNT, 1000, 1e-6))
  #except:
  #  return f"First intrisic calibration failed for {cam_info['imsize']}", None, None

  camData['intrinsics'] = camera_matrix
  camData['dist_coeff'] = distortion_coefficients
  camData['filtered_corners'] = corners
  camData['filtered_ids'] = ids
  return camData

def filter_features_fisheye(cam_info, intrinsic_img, all_features, all_ids):
  f = cam_info['imsize'][0] / (2 * np.tan(np.deg2rad(cam_info["hfov"]/2)))
  print("INTRINSIC CALIBRATION")
  cameraIntrinsics = np.array([[f,  0.0,    cam_info['imsize'][0]/2],
                 [0.0,   f,    cam_info['imsize'][1]/2],
                 [0.0,   0.0,    1.0]])

  distCoeff = np.zeros((12, 1))

  if cam_info["name"] in intrinsic_img:
    raise RuntimeError('This is broken')
    all_features, all_ids, filtered_images = remove_features(filtered_features, filtered_ids, intrinsic_img[cam_info["name"]], image_files)
  else:
    filtered_images = cam_info['images_path']

  filtered_features = all_features
  filtered_ids = all_ids

  cam_info['filtered_ids'] = filtered_ids
  cam_info['filtered_corners'] = filtered_features
  cam_info['intrinsics'] = cameraIntrinsics
  cam_info['dist_coeff'] = distCoeff

  return cam_info

def calibrate_ccm_intrinsics_per_ccm(config: CalibrationConfig, camData: CameraData):
  start = time.time()
  print('starting calibrate_wf')
  ret, cameraIntrinsics, distCoeff, _, _, filtered_ids, filtered_corners, size, coverageImage, all_corners, all_ids = calibrate_wf_intrinsics(config, camData)
  if isinstance(ret, str) and all_ids is None:
    raise RuntimeError('Exception' + ret) # TODO : Handle
  print(f'calibrate_wf took {round(time.time() - start, 2)}s')

  camData['intrinsics'] = cameraIntrinsics
  camData['dist_coeff'] = distCoeff
  camData['size'] = size # (Width, height)
  camData['reprojection_error'] = ret
  print("Reprojection error of {0}: {1}".format(
    camData['name'], ret))

  return camData

def calibrate_ccm_intrinsics(config: CalibrationConfig, camData: CameraData):
  imsize = camData['imsize']
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
      config, allCorners, allIds, imsize, hfov, distortion_flags, camData['intrinsics'], camData['dist_coeff'], camData['name'] == 'thermal')
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
  camData['size'] = size # (Width, height)
  camData['reprojection_error'] = ret
  print("Reprojection error of {0}: {1}".format(
    camData['name'], ret))

  return camData

def calibrate_stereo_perspective(config: CalibrationConfig, obj_pts, left_corners_sampled, right_corners_sampled, left_cam_info, right_cam_info):
  cameraMatrix_l, distCoeff_l, cameraMatrix_r, distCoeff_r, left_distortion_model = left_cam_info['intrinsics'], left_cam_info['dist_coeff'], right_cam_info['intrinsics'], right_cam_info['dist_coeff'], left_cam_info['distortion_model']
  specTranslation = left_cam_info['extrinsics']['specTranslation']
  rot = left_cam_info['extrinsics']['rotation']

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
  obj_pts, left_corners_sampled, right_corners_sampled,
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

def find_stereo_common_features(config: CalibrationConfig, left_cam_info, right_cam_info):
  allIds_l, allIds_r, allCorners_l, allCorners_r = left_cam_info['filtered_ids'], right_cam_info['filtered_ids'], left_cam_info['filtered_corners'], right_cam_info['filtered_corners']
  left_corners_sampled = []
  right_corners_sampled = []
  obj_pts = []

  for i, ids in enumerate(allIds_l): # For ids in all images
    commonIds = np.intersect1d(allIds_l[i], allIds_r[i])
    left_sub_corners = allCorners_l[i][np.isin(allIds_l[i], commonIds)]
    right_sub_corners = allCorners_r[i][np.isin(allIds_r[i], commonIds)]
    obj_pts_sub = config.board.chessboardCorners[commonIds]

    if len(left_sub_corners) > 3 and len(right_sub_corners) > 3:
      obj_pts.append(obj_pts_sub)
      left_corners_sampled.append(left_sub_corners)
      right_corners_sampled.append(right_sub_corners)
    else:
      return -1, "Stereo Calib failed due to less common features"
  return left_corners_sampled, right_corners_sampled, obj_pts

def undistort_points_perspective(allCorners, camInfo):
  return [cv2.undistortPoints(np.array(corners), camInfo['intrinsics'], camInfo['dist_coeff'], P=camInfo['intrinsics']) for corners in allCorners]

def undistort_points_fisheye(allCorners, camInfo):
  return [cv2.fisheye.undistortPoints(np.array(corners), camInfo['intrinsics'], camInfo['dist_coeff'], P=camInfo['intrinsics']) for corners in allCorners]

def remove_and_filter_stereo_features(config:CalibrationConfig, leftCamData: CameraData, rightCamData: CameraData, allCornersAndIds):
  leftCamData['filtered_corners'], leftCamData['filtered_ids'] = estimate_pose_and_filter(config, leftCamData, allCornersAndIds[leftCamData['name']])
  rightCamData['filtered_corners'], rightCamData['filtered_ids'] = estimate_pose_and_filter(config, rightCamData, allCornersAndIds[rightCamData['name']])

  return leftCamData, rightCamData

def calculate_epipolar_error(left_cam_info, right_cam_info, left_cam, right_cam, board_config, extrinsics):

  if extrinsics[0] == -1:
    return -1, extrinsics[1]
  stereoConfig = None
  if board_config['stereo_config']['left_cam'] == left_cam and board_config['stereo_config']['right_cam'] == right_cam: # TODO : Is this supposed to take the last camera pair?
    stereoConfig = {
      'rectification_left': extrinsics[3],
      'rectification_right': extrinsics[4]
    }
  elif board_config['stereo_config']['left_cam'] == right_cam and board_config['stereo_config']['right_cam'] == left_cam:
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
    left_cam_info['extrinsics']['stereo_error'] = extrinsics[0]*np.sqrt(scale) # TODO :Remove one of these
  else:
    print(f"Epipolar error {extrinsics[0]}")
    left_cam_info['extrinsics']['epipolar_error'] = extrinsics[0]
    left_cam_info['extrinsics']['stereo_error'] = extrinsics[0] # TODO : Remove one of these

  left_cam_info['extrinsics']['rotation_matrix'] = extrinsics[1]
  left_cam_info['extrinsics']['translation'] = extrinsics[2]

  return left_cam_info, stereoConfig

def load_camera_data(filepath, cam_info, _cameraModel, ccm_model, model, charucos, resizeWidth, resizeHeight):
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

  images_path = filepath + '/' + cam_info['name']
  if "calib_model" in cam_info:
    cameraModel_ccm, model_ccm = cam_info["calib_model"].split("_")
    if cameraModel_ccm == "fisheye":
      model_ccm == None
    calib_model = cameraModel_ccm
    distortion_model = model_ccm
  else:
    calib_model = _cameraModel
    if cam_info["name"] in ccm_model:
      distortion_model = ccm_model[cam_info["name"]]
    else:
      distortion_model = model

  cam_info['width'] = width
  cam_info['height'] = height
  cam_info['imsize'] = (width, height)
  cam_info['calib_model'] = calib_model
  cam_info['distortion_model'] = distortion_model
  cam_info['images_path'] = images_path
  return cam_info

def getting_features(config: CalibrationConfig, img_path, width, height, charucos=None):
  if charucos:
    allCorners = []
    allIds = []
    for ids, charuco in charucos:
      allCorners.append(charuco)
      allIds.append(ids)
    imsize = (width, height)
    return allCorners, allIds, imsize

  # elif config.features == None or config.features == "charucos":
  #   allCorners, allIds, _, _, imsize, _ = analyze_charuco(config, img_path)
  #   return allCorners, allIds, imsize

  # if config.features == "checker_board":
  #   allCorners, allIds, _, _, imsize, _ = analyze_charuco(config, img_path)
  #   return allCorners, allIds, imsize
  ###### ADD HERE WHAT IT IS NEEDED ######

def get_distortion_flags(distortionModel):
  def is_binary_string(s: str) -> bool:
  # Check if all characters in the string are '0' or '1'
    return all(char in '01' for char in s)
  if distortionModel == None:
    print("Use DEFAULT model")
    flags = cv2.CALIB_RATIONAL_MODEL
  elif is_binary_string(distortionModel):
    flags = cv2.CALIB_RATIONAL_MODEL
    flags += cv2.CALIB_TILTED_MODEL
    flags += cv2.CALIB_THIN_PRISM_MODEL
    binary_number = int(distortionModel, 2)
    # Print the results
    if binary_number == 0:
      clauses_status = [True, True,True, True, True, True, True, True, True]
    else:
      clauses_status = [(binary_number & (1 << i)) != 0 for i in range(len(distortionModel))]
      clauses_status = clauses_status[::-1]
    if clauses_status[0]:
      print("FIX_K1")
      flags += cv2.CALIB_FIX_K1
    if clauses_status[1]:
      print("FIX_K2")
      flags += cv2.CALIB_FIX_K2
    if clauses_status[2]:
      print("FIX_K3")
      flags += cv2.CALIB_FIX_K3
    if clauses_status[3]:
      print("FIX_K4")
      flags += cv2.CALIB_FIX_K4
    if clauses_status[4]:
      print("FIX_K5")
      flags += cv2.CALIB_FIX_K5
    if clauses_status[5]:
      print("FIX_K6")
      flags += cv2.CALIB_FIX_K6
    if clauses_status[6]:
      print("FIX_TANGENT_DISTORTION")
      flags += cv2.CALIB_ZERO_TANGENT_DIST
    if clauses_status[7]:
      print("FIX_TILTED_DISTORTION")
      flags += cv2.CALIB_FIX_TAUX_TAUY
    if clauses_status[8]:
      print("FIX_PRISM_DISTORTION")
      flags += cv2.CALIB_FIX_S1_S2_S3_S4

  elif isinstance(distortionModel, str):
    if distortionModel == "NORMAL":
      print("Using NORMAL model")
      flags = cv2.CALIB_RATIONAL_MODEL
      flags += cv2.CALIB_TILTED_MODEL

    elif distortionModel == "TILTED":
      print("Using TILTED model")
      flags = cv2.CALIB_RATIONAL_MODEL
      flags += cv2.CALIB_TILTED_MODEL

    elif distortionModel == "PRISM":
      print("Using PRISM model")
      flags = cv2.CALIB_RATIONAL_MODEL
      flags += cv2.CALIB_TILTED_MODEL
      flags += cv2.CALIB_THIN_PRISM_MODEL

    elif distortionModel == "THERMAL":
      print("Using THERMAL model")
      flags = cv2.CALIB_RATIONAL_MODEL
      flags += cv2.CALIB_FIX_K3
      flags += cv2.CALIB_FIX_K5
      flags += cv2.CALIB_FIX_K6

  elif isinstance(distortionModel, int):
    print("Using CUSTOM flags")
    flags = distortionModel
  return flags

def calibrate_wf_intrinsics(config: CalibrationConfig, camData: CameraData):
  name = camData['name']
  allCorners = camData['filtered_corners']
  allIds = camData['filtered_ids']
  imsize = camData['imsize']
  hfov = camData['hfov']
  calib_model = camData['calib_model']
  distortionModel = camData['distortion_model']
  cameraIntrinsics = camData['intrinsics']
  distCoeff = camData['dist_coeff']
  
  coverageImage = np.ones(imsize[::-1], np.uint8) * 255
  coverageImage = cv2.cvtColor(coverageImage, cv2.COLOR_GRAY2BGR)
  coverageImage = draw_corners(allCorners, coverageImage)
  if calib_model == 'perspective':
    if config.features == None or config.features == "charucos":
      distortion_flags = get_distortion_flags(distortionModel)
      ret, cameraIntrinsics, distCoeff, rotation_vectors, translation_vectors, filtered_ids, filtered_corners, allCorners, allIds  = calibrate_camera_charuco(
        config, allCorners, allIds, imsize, hfov, distortion_flags, cameraIntrinsics, distCoeff, camData['name'] == 'thermal')

      return ret, cameraIntrinsics, distCoeff, rotation_vectors, translation_vectors, filtered_ids, filtered_corners, imsize, coverageImage, allCorners, allIds
    else:
      return ret, cameraIntrinsics, distCoeff, rotation_vectors, translation_vectors, filtered_ids, filtered_corners, imsize, coverageImage, allCorners, allIds
    #### ADD ADDITIONAL FEATURES CALIBRATION ####
  else:
    if config.features == None or config.features == "charucos":
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

def features_filtering_function(config: CalibrationConfig, rvecs, tvecs, cameraMatrix, distCoeffs, filtered_corners,filtered_id, threshold = None):
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
      objPoints = np.array([config.board.chessboardCorners[id] for id in ids], dtype=np.float32)
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

def detect_charuco_board(config: CalibrationConfig, image: np.array):
  arucoParams = cv2.aruco.DetectorParameters_create()
  arucoParams.minMarkerDistanceRate = 0.01
  corners, ids, rejectedImgPoints = cv2.aruco.detectMarkers(image, config.dictionary, parameters=arucoParams)  # First, detect markers
  marker_corners, marker_ids, refusd, recoverd = cv2.aruco.refineDetectedMarkers(image, config.board, corners, ids, rejectedCorners=rejectedImgPoints)
  # If found, add object points, image points (after refining them)
  if len(marker_corners) > 0:
    ret, corners, ids = cv2.aruco.interpolateCornersCharuco(marker_corners,marker_ids,image, config.board, minMarkers = 1)
    return ret, corners, ids, marker_corners, marker_ids
  else:
    return None, None, None, None, None

def camera_pose_charuco(objpoints: np.array, corners: np.array, ids: np.array, K: np.array, d: np.array, ini_threshold = 2, min_inliers = 0.95, threshold_stepper = 1, max_threshold = 50, skipRANSAC=False):
  objects = []
  while len(objects) < len(objpoints[:,0,0]) * min_inliers:
    if ini_threshold > max_threshold:
      break
    if not skipRANSAC:
      ret, rvec, tvec, objects  = cv2.solvePnPRansac(objpoints, corners, K, d, flags = cv2.SOLVEPNP_P3P, reprojectionError = ini_threshold,  iterationsCount = 10000, confidence = 0.9)
      imgpoints2 = objpoints.copy()

      all_corners = corners.copy()
      all_corners = np.array([all_corners[id[0]] for id in objects])
      imgpoints2 = np.array([imgpoints2[id[0]] for id in objects])
    else:
      imgpoints2 = objpoints.copy()
      all_corners = corners.copy()

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

def analyze_charuco(config: CalibrationConfig, images, scale_req=False, req_resolution=(800, 1280)):
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

    ret, charuco_corners, charuco_ids, marker_corners, marker_ids  = detect_charuco_board(config, gray)

    if charuco_corners is not None and charuco_ids is not None and len(charuco_corners) > 3:

      charuco_corners = cv2.cornerSubPix(gray, charuco_corners,
                winSize=(5, 5),
                zeroZone=(-1, -1),
                criteria=criteria)
      allCorners.append(charuco_corners)  # Charco chess corners
      allIds.append(charuco_ids)  # charuco chess corner id's
      all_marker_corners.append(marker_corners)
      all_marker_ids.append(marker_ids)
    else:
      print(im)
      return f'Failed to detect more than 3 markers on image {im}', None, None, None, None, None

  # imsize = gray.shape[::-1]
  return allCorners, allIds, all_marker_corners, all_marker_ids, gray.shape[::-1], all_recovered

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

def calibrate_camera_charuco(config: CalibrationConfig, allCorners, allIds, imsize, hfov, distortion_flags, cameraIntrinsics, distCoeff, skipRANSAC):
  """
  Calibrates the camera using the dected corners.
  """
  f = imsize[0] / (2 * np.tan(np.deg2rad(hfov/2)))

  threshold = 2 * imsize[1]/800.0
   # check if there are any suspicious corners with high reprojection error
  rvecs = []
  tvecs = []
  index = 0
  max_threshold = 10 + config.initialMaxThreshold * (hfov / 30 + imsize[1] / 800 * 0.2)
  min_inlier = 1 - config.initialMinFiltered * (hfov / 60 + imsize[1] / 800 * 0.2)
  for corners, ids in zip(allCorners, allIds):
    objpts = config.board.chessboardCorners[ids]
    rvec, tvec = camera_pose_charuco(objpts, corners, ids, cameraIntrinsics, distCoeff, skipRANSAC=skipRANSAC)
    tvecs.append(tvec)
    rvecs.append(rvec)
    index += 1
  if skipRANSAC:
    threshold = 60

  # Here we need to get initialK and parameters for each camera ready and fill them inside reconstructed reprojection error per point
  ret = 0.0
  flags = cv2.CALIB_USE_INTRINSIC_GUESS
  flags += distortion_flags

  #   flags = (cv2.CALIB_RATIONAL_MODEL)
  reprojection = []
  removed_errors = []
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
  corner_checker = 0
  previous_ids = []
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
      #if not skipRANSAC:
      filtered_corners, filtered_ids, all_error, removed_corners, removed_ids, removed_error = features_filtering_function(config, rotation_vectors, translation_vectors, camera_matrix, distortion_coefficients, allCorners, allIds, threshold = threshold)
      #else:
      #  filtered_corners, filtered_ids = allCorners, allIds
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
          board=config.board,
          imageSize=imsize,
          cameraMatrix=cameraIntrinsics,
          distCoeffs=distCoeff,
          flags=flags,
          criteria=(cv2.TERM_CRITERIA_EPS & cv2.TERM_CRITERIA_COUNT, 50000, 1e-9))
      except:
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

class StereoCalibration(object):
  """Class to Calculate Calibration and Rectify a Stereo Camera."""

  def __init__(self, traceLevel: float = 1.0, outputScaleFactor: float = 0.5, disableCamera: list = [], model = None,distortion_model = {}, filtering_enable = False, initial_max_threshold = 15, initial_min_filtered = 0.05, calibration_max_threshold = 10):
    self.filtering_enable = filtering_enable
    self.ccm_model = distortion_model
    self.model = model
    self.output_scale_factor = outputScaleFactor
    self.disableCamera = disableCamera
    self.initial_max_threshold = initial_max_threshold
    self.initial_min_filtered = initial_min_filtered
    self.calibration_max_threshold = calibration_max_threshold
    self.calibration_min_filtered = initial_min_filtered

    """Class to Calculate Calibration and Rectify a Stereo Camera."""

  @property
  def _aruco_dictionary(self):
    return self._proxyDict.dictionary

  @property
  def _board(self):
    return self._proxyDict.board

  def calibrate(self, board_config, filepath, square_size, mrk_size, squaresX, squaresY, camera_model, enable_disp_rectify, charucos = {}, intrinsic_img = {}, extrinsic_img = {}):
    """Function to calculate calibration for stereo camera."""
    start_time = time.time()
    # init object data
    self._cameraModel = camera_model
    self._data_path = filepath
    self._proxyDict = ProxyDict(squaresX, squaresY, square_size, mrk_size, aruco.DICT_4X4_1000)
    self.squaresX = squaresX
    self.squaresY = squaresY
    self.squareSize = square_size
    self.markerSize = mrk_size
    self.stereocalib_criteria = (cv2.TERM_CRITERIA_COUNT +
                   cv2.TERM_CRITERIA_EPS, 300, 1e-9)

    self.cams = []
    features = None

    resizeWidth, resizeHeight = 1280, 800

    self.disableCamera = []
    activeCameras: List[Tuple[str, CameraData]] = [(cam, cam_info) for cam, cam_info in board_config['cameras'].items() if not cam_info['name'] in self.disableCamera]
    
    config = CalibrationConfig(
      ProxyDict(squaresX, squaresY, square_size, mrk_size, aruco.DICT_4X4_1000),
      self.filtering_enable, self.ccm_model, self.disableCamera, self.initial_max_threshold, self.initial_min_filtered, self.calibration_max_threshold, self.calibration_min_filtered,
      camera_model, (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 300, 1e-9), None
    )

    stereoPairs = []
    for camera, camInfo in activeCameras:
      if str(camInfo['name']) in self.disableCamera \
        or not 'extrinsics' in camInfo \
        or not 'to_cam' in camInfo['extrinsics'] \
        or str(board_config['cameras'][camInfo['extrinsics']['to_cam']]['name']) in self.disableCamera:
        continue
      stereoPairs.append([camera, camInfo['extrinsics']['to_cam']])

    for cam, camData in activeCameras:
      camData = load_camera_data(filepath, camData, self._cameraModel, self.ccm_model, self.model, charucos, resizeWidth, resizeHeight)

    camInfos = {}
    stereoConfigs = []
    pw = ParallelWorker(1)

    for cam, camData in activeCameras:
      if PER_CCM:
        camData, features, ids = pw.run(get_features, config, camData, intrinsic_img[camData['name']])[:3]
        if self._cameraModel == "fisheye":
          camData = pw.run(filter_features_fisheye, camData, intrinsic_img) # TODO : Input types are wrong
        else:
          features, ids = pw.map(estimate_pose_and_filter_single, config, camData, features, ids)[:2]
    # camData['features'] = corners2[valid_mask].reshape(-1, 1, 2)
    # camData['ids'] = valid_ids.reshape(-1, 1).astype(np.int32), corners2[removed_mask]
    # return camData

          camData = pw.run(calibrate_charuco, config, camData, features, ids)
        camData = pw.run(calibrate_ccm_intrinsics_per_ccm, config, camData)
        camInfos[cam] = camData
      else:
        camData = calibrate_ccm_intrinsics(config, camData)

    for left, right in stereoPairs:
      left_cam_info = camInfos[left]
      right_cam_info = camInfos[right]

      if PER_CCM and EXTRINSICS_PER_CCM:
        left_cam_info, right_cam_info = pw.run(remove_and_filter_stereo_features, config, left_cam_info, right_cam_info, extrinsic_img)[:2]

      left_corners_sampled, right_corners_sampled, obj_pts= pw.run(find_stereo_common_features, config, left_cam_info, right_cam_info)[:3]

      if PER_CCM and EXTRINSICS_PER_CCM:
        if left_cam_info['calib_model'] == "perspective":
          left_corners_sampled = pw.run(undistort_points_perspective, left_corners_sampled, left_cam_info)
          right_corners_sampled = pw.run(undistort_points_perspective, right_corners_sampled, right_cam_info)
        else:
          left_corners_sampled = pw.run(undistort_points_fisheye, left_corners_sampled, left_cam_info)
          right_corners_sampled = pw.run(undistort_points_fisheye, right_corners_sampled, right_cam_info)

        if features == None or features == "charucos":
          extrinsics = pw.run(calibrate_stereo_perspective_per_ccm, config, obj_pts, left_corners_sampled, right_corners_sampled, left_cam_info, right_cam_info)
        #### ADD OTHER CALIBRATION METHODS ###
      else:
        if self._cameraModel == 'perspective':
          extrinsics = pw.run(calibrate_stereo_perspective, config, obj_pts, left_corners_sampled, right_corners_sampled, left_cam_info, right_cam_info)
        elif self._cameraModel == 'fisheye':
          extrinsics = pw.run(calibrate_stereo_fisheye, config, obj_pts, left_corners_sampled, right_corners_sampled, left_cam_info, right_cam_info)
      camData, stereo_config = pw.run(calculate_epipolar_error, left_cam_info, right_cam_info, left, right, board_config, extrinsics)[:2]
      camInfos[left] = camData
      stereoConfigs.append(stereo_config)

    pw.execute()

    # Extract camera info structs and stereo config
    for cam, camInfo in camInfos.items():
      board_config['cameras'][cam] = camInfo.ret()

    for stereoConfig in stereoConfigs:
      if stereoConfig.ret():
        board_config['stereo_config'].update(stereoConfig.ret())

    return 1, board_config