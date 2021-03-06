# Copyright (C) 2020 Matthew Cooper

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import glob

import numpy as np
import torch

import dicom_create_rs_file
import dicom_network_model_export_scu as scu
import dicom_utils
import mask
from model2D import UNet


def load_model(checkpoint_path):
    model = UNet.load_from_checkpoint(checkpoint_path=checkpoint_path)
    model = model.eval().cuda(device=0)
    model.freeze()
    return model


def load_inputs(dicom_series):
    pixel_arrays = dicom_utils.get_pixel_arrays(dicom_series)
    # Shape (n, x, y) to (n, 1, x, y) for Torch input
    pixel_arrays = np.expand_dims(pixel_arrays, axis=1)
    # Normalise
    pixel_arrays = (pixel_arrays -
                    np.mean(pixel_arrays)) / np.std(pixel_arrays)
    pixel_arrays = pixel_arrays.astype("float32")
    pixel_arrays = torch.from_numpy(pixel_arrays)
    return pixel_arrays


def predict_to_structure(dicom, prediction):
    x_grid, y_grid, ct_size = mask.get_grid(dicom)
    z_position = float(dicom.SliceLocation)
    # Drop the batch index [1, 1, x_grid, y_grid] -> [1, x_grid, y_grid]
    # NOTE prediction[..., 0] for TensorFlow
    slice_contours = mask.get_contours_from_mask(x_grid, y_grid,
                                                 prediction[0, ...])

    # [x1 y1 x2 y2 ... ] to [x1 y1 z x2 y2 z ...]
    slice_structure_xyz = []
    for roi in slice_contours:
        roi_xyz = []
        for xy_point in roi:
            xyz_point = [*xy_point, z_position]
            roi_xyz = roi_xyz + xyz_point
        slice_structure_xyz.append(roi_xyz)
    return slice_structure_xyz


def convert_to_dicom_rs(dicom_series, predictions, root_uid):
    structures = []
    for dicom, prediction in zip(dicom_series, predictions):
        structure = predict_to_structure(dicom, prediction)
        structures.append(structure)
    assert len(structures) == len(dicom_series)

    dicom_structure_file = dicom_create_rs_file.create_rs_file(
        dicom_series, structures, root_uid)
    return dicom_structure_file


def infer_contours(study_path,
                   root_uid,
                   checkpoint_path,
                   convert_to_dicom=True):

    dicom_paths = glob.glob(study_path + "/*.dcm")
    dicom_files = dicom_utils.read_dicom_paths(dicom_paths, force=True)
    dicom_files = dicom_utils.add_transfer_syntax(dicom_files)
    dicom_series, *rest = dicom_utils.filter_dicom_files(dicom_files)
    dicom_series = dicom_utils.sort_slice_location(dicom_series)

    pixel_arrays = load_inputs(dicom_series)

    model = load_model(checkpoint_path)

    model_output = []
    for i, x in enumerate(pixel_arrays):
        x = x[np.newaxis, ...]
        output = model(x.cuda(0))
        model_output.append(output.cpu().numpy())

    model_output = np.array(model_output)
    predictions = np.round(model_output)

    if convert_to_dicom:
        dicom_structure_file = convert_to_dicom_rs(dicom_series, predictions,
                                                   root_uid)
        return dicom_structure_file
    else:
        return predictions


if __name__ == "__main__":
    study_path = "../test_dicom_dataset/"
    root_uid = "1.2.826.0.1.3680043.8.498."
    checkpoint_path = "/home/matthew/lightning_proto/lightning_proto/lightning_logs/version_1/checkpoints/epoch=0-step=128.ckpt"
    dicom_structure_file = infer_contours(study_path, root_uid,
                                          checkpoint_path)
