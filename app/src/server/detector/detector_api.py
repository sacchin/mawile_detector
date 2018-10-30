#!/usr/bin/python
# -*- coding: utf-8 -*-

import os
import numpy as np
import pickle
import json
import time
import sys
from flask import Blueprint, render_template, request, jsonify, current_app
from werkzeug import secure_filename
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from keras.preprocessing import image
from imageio import imread
from scipy.misc import imresize
from keras.applications.imagenet_utils import preprocess_input
from server.detector.ssd import SSD300
from server.detector.ssd_utils import BBoxUtility

app = Blueprint('detector_api', __name__, static_folder='static')
ALLOWED_EXTENSIONS = set(['png', 'jpg', 'jpeg', 'gif'])


def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1] in ALLOWED_EXTENSIONS


def dir_preparation(upload_path):
    if os.path.exists(upload_path):
        logger = current_app.logger
        logger.info("upload folder exists")
    else:
        os.makedirs(upload_path)


def get_explanatory_json():
    explanatory_file = current_app.config['EXPLAINATORY_TEXT_FILE']
    with open(explanatory_file, 'r') as f:
        explanatory = json.load(f)

    no = explanatory['no']
    name = explanatory['name']
    classification = explanatory['classification']
    text_list = explanatory['text_list']
    mod = int(time.time() * 100) % len(text_list)

    return {
        "no": no,
        "name": name,
        "classification": classification,
        "explanatory": text_list[mod]
    }


@app.route('/index')
def index():
    return render_template('detect.html')


@app.route('/test', methods=['GET', 'POST'])
def detect():
    save_path = current_app.config['SAVE_PATH']
    if request.method == 'POST':
        logger = current_app.logger
        try:
            if 'file' not in request.files:
                return jsonify(ResultSet={
                    "result": "ng",
                    "message": "no image"
                })

            img_file = request.files['file']
            saved, filename = saveImage(save_path, img_file)

            logger.info("save path is {}".format(save_path))
            logger.info("file name is {}".format(img_file.filename))

            if saved:
                uploadGoogleDrive(save_path, filename)

                box = ssd_predict(save_path, filename)
                ej = get_explanatory_json()

                return jsonify(
                    ResultSet={
                        "result": "ok",
                        "filename": filename,
                        "box": box,
                        "explanatory": ej
                    })
            else:
                return jsonify(ResultSet={"result": "ng", "message": filename})

        except NameError as err:
            logger.error("NameError: {0}".format(err))
            return jsonify(ResultSet={"result": "ng", "message": "NameError"})
        except OSError as err:
            logger.error("OSerror: {0}".format(err))
            return jsonify(ResultSet={"result": "ng", "message": "OSError"})
        except ValueError as err:
            logger.error("ValueError: {0}".format(err))
            return jsonify(ResultSet={"result": "ng", "message": "ValueError"})
        except TypeError as err:
            logger.error("TypeError: {0}".format(err))
            return jsonify(ResultSet={"result": "ng", "message": "TypeError"})
        except:
            logger.error("Unexpected error:{}".format(sys.exc_info()[0]))
            return jsonify(ResultSet={"result": "ng", "box": "except"})

    return jsonify(ResultSet={"result": "ng", "message": "only support post."})


def saveImage(save_path, img_file):
    time_id = time.time()
    img_file.filename = "{}_{}.png".format(img_file.filename, time_id)
    if img_file and allowed_file(img_file.filename):
        filename = secure_filename(img_file.filename)
        dir_preparation(save_path)
        img_file.save(os.path.join(save_path, filename))
        return (True, filename)

    return (False, "image file is null or file name isn't correct")


def uploadGoogleDrive(save_path, filename):
    folder_id = current_app.config['DRIVE_FOLDER_ID']

    gauth = GoogleAuth()
    gauth.LocalWebserverAuth()
    drive = GoogleDrive(gauth)

    f = drive.CreateFile({
        'title':
        filename,
        'mimeType':
        'image/jpeg',
        'parents': [{
            'kind': 'drive#fileLink',
            'id': folder_id
        }]
    })
    f.SetContentFile(os.path.join(save_path, filename))
    f.Upload()


def ssd_predict_mock(save_path, filename):
    box_array = []
    box_array.append({
        'xmin': 0,
        'ymin': 0,
        'xmax': 50,
        'ymax': 50,
        'label': 'l1',
        'display_txt': 'l1'
    })

    return box_array


def ssd_predict(save_path, filename):
    logger = current_app.logger
    
    weight_file = current_app.config['WEIGHT_FILE']
    prior_pkl_file = current_app.config['PRIOR_PICKLE_FILE']
    input_shape = (300, 300, 3)
    NUM_CLASSES = 21

    model = SSD300(input_shape, num_classes=NUM_CLASSES)
    model.load_weights(weight_file, by_name=True)

    priors = pickle.load(open(prior_pkl_file, 'rb'))
    bbox_util = BBoxUtility(NUM_CLASSES, priors)

    img_path = os.path.join(save_path, filename)
    img = image.load_img(img_path, target_size=(300, 300))
    img = image.img_to_array(img)
    inputs = preprocess_input(np.array([img.copy()]))
    origin_image = imread(img_path)

    preds = model.predict(inputs, batch_size=1, verbose=1)
    results = bbox_util.detection_out(preds)

    det_label = results[0][:, 0]
    det_conf = results[0][:, 1]
    det_xmin = results[0][:, 2]
    det_ymin = results[0][:, 3]
    det_xmax = results[0][:, 4]
    det_ymax = results[0][:, 5]

    # Get detections with confidence higher than 0.6.
    top_indices = [i for i, conf in enumerate(det_conf) if conf >= 0.6]

    top_conf = det_conf[top_indices]
    top_label_indices = det_label[top_indices].tolist()
    top_xmin = det_xmin[top_indices]
    top_ymin = det_ymin[top_indices]
    top_xmax = det_xmax[top_indices]
    top_ymax = det_ymax[top_indices]

    box_array = []
    for i in range(top_conf.shape[0]):
        xmin = int(round(top_xmin[i] * origin_image.shape[1]))
        ymin = int(round(top_ymin[i] * origin_image.shape[0]))
        xmax = int(round(top_xmax[i] * origin_image.shape[1]))
        ymax = int(round(top_ymax[i] * origin_image.shape[0]))
        label = int(top_label_indices[i])
        display_txt = '{:0.2f}, {}'.format(top_conf[i], label)
        box_array.append({
            'xmin': xmin,
            'ymin': ymin,
            'xmax': xmax,
            'ymax': ymax,
            'label': label,
            'display_txt': display_txt
        })

    logger.info("detect {}. result is {}".format(filename, box_array))
    return box_array
