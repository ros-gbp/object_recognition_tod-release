#!/usr/bin/env python
"""
Module defining the TOD detector to find objects in a scene
"""

from ecto_image_pipeline.base import RescaledRegisteredDepth
from ecto_opencv import features2d, highgui, imgproc, calib
from ecto_opencv.calib import DepthTo3d
from ecto_opencv.features2d import FeatureDescriptor
from object_recognition_core.boost.interface import Models
from object_recognition_core.db.object_db import ObjectDb
from object_recognition_core.pipelines.detection import DetectionPipeline
from object_recognition_core.utils import json_helper
from object_recognition_tod import ecto_detection
import ecto

try:
    import ecto_ros.ecto_ros_main
    import ecto_ros.ecto_sensor_msgs as ecto_sensor_msgs
    ECTO_ROS_FOUND = True
except ImportError:
    ECTO_ROS_FOUND = False

class TodDetector(ecto.BlackBox):
    feature_descriptor = FeatureDescriptor
    descriptor_matcher = ecto_detection.DescriptorMatcher
    guess_generator = ecto_detection.GuessGenerator
    passthrough = ecto.PassthroughN
    _depth_map = RescaledRegisteredDepth
    _points3d = DepthTo3d
    if ECTO_ROS_FOUND:
        message_cvt = ecto_ros.ecto_ros_main.Mat2Image

    def __init__(self, subtype, parameters, model_documents, object_db, visualize=False, **kwargs):
        self._subtype = subtype
        self._parameters = parameters
        self._model_documents = model_documents
        self._object_db = object_db

        self._visualize = visualize

        ecto.BlackBox.__init__(self, **kwargs)

    def declare_params(self, p):
        if ECTO_ROS_FOUND:
            p.forward('rgb_frame_id', cell_name='message_cvt', cell_key='frame_id')
        #p.forward('model_documents', cell_name='descriptor_matcher', cell_key='model_documents')

    def declare_io(self, _p, i, o):
        self.passthrough = ecto.PassthroughN(items=dict(image='An image',
                                                   K='The camera matrix'
                                                   )
                                        )
        i.forward(['image', 'K'], cell_name='passthrough', cell_key=['image', 'K'])
        i.forward('mask', cell_name='feature_descriptor', cell_key='mask')
        i.forward('depth', cell_name='_depth_map', cell_key='depth')

        o.forward('pose_results', cell_name='guess_generator', cell_key='pose_results')
        o.forward('keypoints', cell_name='feature_descriptor', cell_key='keypoints')

    def configure(self, p, _i, _o):
        # get the feature parameters
        if 'feature' not in self._parameters:
            raise RuntimeError("You must supply feature parameters for TOD.")
        feature_params = self._parameters.get("feature")
        # get the descriptor parameters
        if 'descriptor' not in self._parameters:
            raise RuntimeError("You must supply descriptor parameters for TOD.")
        descriptor_params = self._parameters.get('descriptor')

        self.feature_descriptor = FeatureDescriptor(json_feature_params=json_helper.dict_to_cpp_json_str(feature_params),
                                json_descriptor_params=json_helper.dict_to_cpp_json_str(descriptor_params))
        self.descriptor_matcher = ecto_detection.DescriptorMatcher("Matcher",
                                search_json_params=json_helper.dict_to_cpp_json_str(self._parameters['search']),
                                model_documents=self._model_documents)
        if ECTO_ROS_FOUND:
            self.message_cvt = ecto_ros.ecto_ros_main.Mat2Image()

        guess_params = self._parameters['guess'].copy()
        guess_params['visualize'] = self._visualize
        guess_params['db'] = self._object_db

        self.guess_generator = ecto_detection.GuessGenerator("Guess Gen", **guess_params)
        self._depth_map = RescaledRegisteredDepth()
        self._points3d = DepthTo3d()

    def connections(self):
        # Rescale the depth image and convert to 3d
        graph = [ self.passthrough['image'] >> self._depth_map['image'],
                  self._depth_map['depth'] >>  self._points3d['depth'],
                  self.passthrough['K'] >> self._points3d['K'],
                  self._points3d['points3d'] >> self.guess_generator['points3d'] ]
        # make sure the inputs reach the right cells
        if 'depth' in self.feature_descriptor.inputs.keys():
            graph += [ self._depth_map['depth'] >> self.feature_descriptor['depth']]

        graph += [ self.passthrough['image'] >> self.feature_descriptor['image'],
                   self.passthrough['image'] >> self.guess_generator['image'] ]

        graph += [ self.descriptor_matcher['spans'] >> self.guess_generator['spans'],
                   self.descriptor_matcher['object_ids'] >> self.guess_generator['object_ids'] ]

        graph += [ self.feature_descriptor['keypoints'] >> self.guess_generator['keypoints'],
                   self.feature_descriptor['descriptors'] >> self.descriptor_matcher['descriptors'],
                   self.descriptor_matcher['matches', 'matches_3d'] >> self.guess_generator['matches', 'matches_3d'] ]

        cvt_color = imgproc.cvtColor(flag=imgproc.RGB2GRAY)

        if self._visualize or ECTO_ROS_FOUND:
            draw_keypoints = features2d.DrawKeypoints()
            graph += [ self.passthrough['image'] >> cvt_color[:],
                           cvt_color[:] >> draw_keypoints['image'],
                           self.feature_descriptor['keypoints'] >> draw_keypoints['keypoints']
                           ]

        if self._visualize:
            # visualize the found keypoints
            image_view = highgui.imshow(name="RGB")
            keypoints_view = highgui.imshow(name="Keypoints")


            graph += [ self.passthrough['image'] >> image_view['image'],
                       draw_keypoints['image'] >> keypoints_view['image']
                           ]

            pose_view = highgui.imshow(name="Pose")
            pose_drawer = calib.PosesDrawer()

            # draw the poses
            graph += [ self.passthrough['image', 'K'] >> pose_drawer['image', 'K'],
                       self.guess_generator['Rs', 'Ts'] >> pose_drawer['Rs', 'Ts'],
                       pose_drawer['output'] >> pose_view['image'] ]

        if ECTO_ROS_FOUND:
            ImagePub = ecto_sensor_msgs.Publisher_Image
            pub_features = ImagePub("Features Pub", topic_name='features')
            graph += [ draw_keypoints['image'] >> self.message_cvt[:],
                       self.message_cvt[:] >> pub_features[:] ]

        return graph

########################################################################################################################

class TodDetectionPipeline(DetectionPipeline):
    @classmethod
    def config_doc(cls):
        return  """
                    # The subtype can be any YAML that will help differentiate
                    # between TOD methods: we usually use the descriptor name
                    # but anything like parameters could be used
                    subtype:
                        type: ""
                    # TOD requires several parameters
                    parameters:
                        feature:
                            # a type is required, this is the name of the feature cell
                            type: ""
                            # you also need a module in which your feature cell is included
                            # usually, that will be from 'ecto_opencv.features2d'
                            module: ""
                        descriptor:
                            # same a type is required
                            type: ""
                            # same a module is required
                            module: ""
                        search:
                            # a type is required for the nearest neighbor search
                            # The only supported value is 'LSH' for now as we've only been playing with ORB
                            # Other types could easily be implemented, please file a bug report
                            type: ""
                        guess:
                            # This is the number of RANSAC iterations: we use 500 usually
                            n_ransac_iterations: 500
                            # The minimum number of inliers when performing RANSAC: that should not be too small
                            # as is common with RANSAC in pose estimation. 8 works pretty well usually
                            min_inliers: 8
                        # This is the error we assume that comes from collecting the depth data, in meters
                        sensor_error: 0.01
                        # Then come the standard DB parameters
                        db:
                            # this can be from: 'CouchDB', 'filesystem'
                            type: "CouchDB"
                            # where the DB is located
                            root: "http://localhost:5984"
                            # the collection in which to store everything (training, capture data)
                            collection: "object_recognition"
                """

    @classmethod
    def type_name(cls):
        return 'TOD'

    @classmethod
    def detector(self, *args, **kwargs):
        visualize = kwargs.pop('visualize', False)
        subtype = kwargs.pop('subtype')
        parameters = kwargs.pop('parameters')
        object_ids = parameters['object_ids']
        object_db = ObjectDb(parameters['db'])
        model_documents = Models(object_db, object_ids, self.type_name(), json_helper.dict_to_cpp_json_str(subtype))
        return TodDetector(subtype, parameters, model_documents, object_db, visualize, **kwargs)
