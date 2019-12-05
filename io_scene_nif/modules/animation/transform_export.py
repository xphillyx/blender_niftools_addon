"""This script contains classes to help export blender bone or object level transform(ation) animations into NIF controllers."""

# ***** BEGIN LICENSE BLOCK *****
#
# Copyright © 2019, NIF File Format Library and Tools contributors.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#
#    * Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials provided
#      with the distribution.
#
#    * Neither the name of the NIF File Format Library and Tools
#      project nor the names of its contributors may be used to endorse
#      or promote products derived from this software without specific
#      prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# ***** END LICENSE BLOCK *****

import bpy
import mathutils

from pyffi.formats.nif import NifFormat

from io_scene_nif.modules import armature
from io_scene_nif.modules.object.block_registry import block_store
from io_scene_nif.modules.animation import animation_export
from io_scene_nif.utility import nif_utils
from io_scene_nif.utility.util_logging import NifLog
from io_scene_nif.utility.util_global import NifOp


class TransformAnimation:
    
    def __init__(self, parent):
        self.nif_export = parent
        self.fps = bpy.context.scene.render.fps

    @staticmethod
    def iter_frame_key(fcurves, MathutilsClass):
        """
        Iterator that yields a tuple of frame and key for all fcurves.
        Assumes the fcurves are sampled at the same time and all have the same amount of keys
        Return the key in the desired MathutilsClass
        """
        for point in zip(*[fcu.keyframe_points for fcu in fcurves]):
            frame = point[0].co[0]
            key = [k.co[1] for k in point]
            yield frame, MathutilsClass(key)

    def export_transforms(self, parent_block, b_obj=None, bone=None):
        """
        If called on b_obj=None and bone=None it should save an empty controller.
        If called on an b_obj = type(armature), it expects a bone too.
        If called on an object, with bone=None, it exports object level animation.
        """
        
        target_name  = ""
        # sometimes we need to export an empty keyframe... 
        scale_curve = []
        quat_curve = []
        euler_curve = []
        trans_curve = []

        exp_fcurves = []

        # just for more detailed error reporting later on
        bonestr = ""

        # we have either skeletal or object animation
        if b_obj and b_obj.animation_data and b_obj.animation_data.action:
            action = b_obj.animation_data.action

            # skeletal animation - with bone correction & coordinate corrections
            if bone and bone.name in action.groups:
                # get bind matrix for bone or object
                bind_matrix = self.nif_export.objecthelper.get_object_bind(bone)
                exp_fcurves = action.groups[bone.name].channels
                # just for more detailed error reporting later on
                bonestr = " in bone " + bone.name
                target_name = self.nif_export.objecthelper.get_full_name(bone)
            # object level animation - no coordinate corrections
            elif not bone:
                # raise error on any objects parented to bones
                if b_obj.parent and b_obj.parent_type == "BONE":
                    raise nif_utils.NifError( "{} is parented to a bone AND has animations. The nif format does not support this!".format(b_obj.name))

                target_name = self.nif_export.objecthelper.get_full_name(b_obj)
                # we have either a root object (Scene Root), in which case we take the coordinates without modification
                # or a generic object parented to an empty = node
                # objects may have an offset from their parent that is not apparent in the user input (ie. UI values and keyframes)
                # we want to export matrix_local, and the keyframes are in matrix_basis, so do:
                # matrix_local = matrix_parent_inverse * matrix_basis
                bind_matrix = b_obj.matrix_parent_inverse
                exp_fcurves = [fcu for fcu in action.fcurves if
                               fcu.data_path in ("rotation_quaternion", "rotation_euler", "location", "scale")]
            # decompose the bind matrix
            if exp_fcurves:
                bind_scale, bind_rot, bind_trans = nif_utils.decompose_srt(bind_matrix)
                bind_rot = bind_rot.to_4x4()
            start_frame, stop_frame = action.frame_range

        # we are supposed to export an empty controller
        else:
            # only set frame range
            start_frame = bpy.context.scene.frame_start
            stop_frame = bpy.context.scene.frame_end

        n_kfc, n_kfi = self.nif_export.animationhelper.create_controller(parent_block, target_name)

        # fill in the non-trivial values
        animation_export.set_flags_and_timing(n_kfc, exp_fcurves, start_frame, stop_frame)

        # get the desired fcurves for each data type from exp_fcurves
        quaternions = [fcu for fcu in exp_fcurves if fcu.data_path.endswith("quaternion")]
        translations = [fcu for fcu in exp_fcurves if fcu.data_path.endswith("location")]
        eulers = [fcu for fcu in exp_fcurves if fcu.data_path.endswith("euler")]
        scales = [fcu for fcu in exp_fcurves if fcu.data_path.endswith("scale")]

        # go over all fcurves collected above and transform and store all keys
        if scales:
            # just use the first scale curve and assume even scale over all curves
            for frame, scale in self.iter_frame_key(scales, mathutils.Vector):
                scale_curve.append((frame, scale[0]))

        if quaternions:
            if len(quaternions) != 4:
                raise nif_utils.NifError("Incomplete ROT key set {} for action {}".format(bonestr, action.name))
            else:
                for frame, quat in self.iter_frame_key(quaternions, mathutils.Quaternion):
                    quat = armature.export_keymat(bind_rot, quat.to_matrix().to_4x4(), bone).to_quaternion()
                    quat_curve.append((frame, quat))

        if eulers:
            if len(eulers) != 3:
                raise nif_utils.NifError("Incomplete Euler key set {} for action {}".format(bonestr, action.name))
            else:
                for frame, euler in self.iter_frame_key(eulers, mathutils.Euler):
                    keymat = armature.export_keymat(bind_rot, euler.to_matrix().to_4x4(), bone)
                    euler = keymat.to_euler("XYZ", euler)
                    euler_curve.append((frame, euler))

        if translations:
            if len(translations) != 3:
                raise nif_utils.NifError("Incomplete LOC key set{} for action {}".format(bonestr, action.name))
            else:
                for frame, trans in self.iter_frame_key(translations, mathutils.Vector):
                    keymat = armature.export_keymat(bind_rot, mathutils.Matrix.Translation(trans), bone)
                    trans = keymat.to_translation() + bind_trans
                    trans_curve.append((frame, trans))

        # finally we can export the data calculated above
        if n_kfi and max(len(quat_curve), len(euler_curve), len(trans_curve), len(scale_curve)) <= 1:
            # only add data if number of keys is > 1
            # (see importer comments with import_kf_root: a single frame
            # keyframe denotes an interpolator without further data)
            # insufficient keys, so set the data and we're done!
            if trans_curve:
                trans = trans_curve[0][1]
                n_kfi.translation.x = trans[0]
                n_kfi.translation.y = trans[1]
                n_kfi.translation.z = trans[2]
            if quat_curve:
                quat = quat_curve[0][1]
                n_kfi.rotation.x = quat.x
                n_kfi.rotation.y = quat.y
                n_kfi.rotation.z = quat.z
                n_kfi.rotation.w = quat.w
            elif euler_curve:
                quat = euler_curve[0][1].to_quaternion()
                n_kfi.rotation.x = quat.x
                n_kfi.rotation.y = quat.y
                n_kfi.rotation.z = quat.z
                n_kfi.rotation.w = quat.w
            # ignore scale for now...
            n_kfi.scale = 1.0
            # done!
            return

        # add the keyframe data
        if not n_kfi:
            n_kfd = block_store.create_block("NiKeyframeData", exp_fcurves)
            n_kfc.data = n_kfd
        else:
            # number of frames is > 1, so add transform data
            n_kfd = block_store.create_block("NiTransformData", exp_fcurves)
            n_kfi.data = n_kfd

        # TODO [animation] support other interpolation modes, get interpolation from blender?
        #                  probably requires additional data like tangents and stuff

        # save all nif keys
        if euler_curve:
            n_kfd.rotation_type = NifFormat.KeyType.XYZ_ROTATION_KEY
            n_kfd.num_rotation_keys = 1  # *NOT* len(frames) this crashes the engine!
            for i, coord in enumerate(n_kfd.xyz_rotations):
                coord.num_keys = len(euler_curve)
                coord.interpolation = NifFormat.KeyType.LINEAR_KEY
                coord.keys.update_size()
                for key, (frame, euler) in zip(coord.keys, euler_curve):
                    key.time = frame / self.fps
                    key.value = euler[i]
        elif quat_curve:
            n_kfd.rotation_type = NifFormat.KeyType.LINEAR_KEY
            n_kfd.num_rotation_keys = len(quat_curve)
            n_kfd.quaternion_keys.update_size()
            for key, (frame, quat) in zip(n_kfd.quaternion_keys, quat_curve):
                key.time = frame / self.fps
                key.value.w = quat.w
                key.value.x = quat.x
                key.value.y = quat.y
                key.value.z = quat.z

        n_kfd.translations.interpolation = NifFormat.KeyType.LINEAR_KEY
        n_kfd.translations.num_keys = len(trans_curve)
        n_kfd.translations.keys.update_size()
        for key, (frame, trans) in zip(n_kfd.translations.keys, trans_curve):
            key.time = frame / self.fps
            key.value.x, key.value.y, key.value.z = trans

        n_kfd.scales.interpolation = NifFormat.KeyType.LINEAR_KEY
        n_kfd.scales.num_keys = len(scale_curve)
        n_kfd.scales.keys.update_size()
        for key, (frame, scale) in zip(n_kfd.scales.keys, scale_curve):
            key.time = frame / self.fps
            key.value = scale
