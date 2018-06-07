# Simaese Mask R-CNN Utils

import tensorflow as tf
import sys
import os
import time
import random
import numpy as np
import skimage.io
import skimage.transform as skt
import imgaug
import matplotlib.pyplot as plt
plt.rcParams['figure.figsize'] = (12.0, 6.0)

MASK_RCNN_MODEL_PATH = '/gpfs01/bethge/home/cmichaelis/tf-models/Mask_RCNN/'

if MASK_RCNN_MODEL_PATH not in sys.path:
    sys.path.append(MASK_RCNN_MODEL_PATH)
    
from samples.coco import coco
from mrcnn import utils
from mrcnn import model as modellib
from mrcnn import visualize  
    
import warnings
warnings.filterwarnings("ignore")
    
### Data Generator ###
    
def get_one_target(category, dataset, config, augmentation=None):

    # Get index with corresponding images for each category
    category_image_index = dataset.category_image_index
    # Draw a random image
    random_image_id = np.random.choice(category_image_index[category])
    # Load image    
    target_image, target_image_meta, target_class_ids, target_boxes, target_masks = \
        modellib.load_image_gt(dataset, config, random_image_id, augmentation=augmentation,
                      use_mini_mask=config.USE_MINI_MASK)

    box_ind = np.random.choice(np.where(target_class_ids == category)[0])   
    tb = target_boxes[box_ind,:]
    target = target_image[tb[0]:tb[2],tb[1]:tb[3],:]
    target, window, scale, padding, crop = utils.resize_image(
        target,
        min_dim=config.TARGET_MIN_DIM,
        min_scale=config.IMAGE_MIN_SCALE, #Same scaling as the image
        max_dim=config.TARGET_MAX_DIM,
        mode=config.IMAGE_RESIZE_MODE) #Same output format as the image
    
    
    return target

def siamese_data_generator(dataset, config, shuffle=True, augmentation=imgaug.augmenters.Fliplr(0.5), random_rois=0,
                   batch_size=1, detection_targets=False, diverse=0):
    """A generator that returns images and corresponding target class ids,
    bounding box deltas, and masks.
    dataset: The Dataset object to pick data from
    config: The model config object
    shuffle: If True, shuffles the samples before every epoch
    augment: If True, applies image augmentation to images (currently only
             horizontal flips are supported)
    random_rois: If > 0 then generate proposals to be used to train the
                 network classifier and mask heads. Useful if training
                 the Mask RCNN part without the RPN.
    batch_size: How many images to return in each call
    detection_targets: If True, generate detection targets (class IDs, bbox
        deltas, and masks). Typically for debugging or visualizations because
        in trainig detection targets are generated by DetectionTargetLayer.
    diverse: Float in [0,1] indicatiing probability to draw a target
        from any random class instead of one from the image classes
    Returns a Python generator. Upon calling next() on it, the
    generator returns two lists, inputs and outputs. The containtes
    of the lists differs depending on the received arguments:
    inputs list:
    - images: [batch, H, W, C]
    - image_meta: [batch, size of image meta]
    - rpn_match: [batch, N] Integer (1=positive anchor, -1=negative, 0=neutral)
    - rpn_bbox: [batch, N, (dy, dx, log(dh), log(dw))] Anchor bbox deltas.
    - gt_class_ids: [batch, MAX_GT_INSTANCES] Integer class IDs
    - gt_boxes: [batch, MAX_GT_INSTANCES, (y1, x1, y2, x2)]
    - gt_masks: [batch, height, width, MAX_GT_INSTANCES]. The height and width
                are those of the image unless use_mini_mask is True, in which
                case they are defined in MINI_MASK_SHAPE.
    outputs list: Usually empty in regular training. But if detection_targets
        is True then the outputs list contains target class_ids, bbox deltas,
        and masks.
    """
    b = 0  # batch item index
    image_index = -1
    image_ids = np.copy(dataset.image_ids)
    error_count = 0

    # Anchors
    # [anchor_count, (y1, x1, y2, x2)]
    backbone_shapes = modellib.compute_backbone_shapes(config, config.IMAGE_SHAPE)
    anchors = utils.generate_pyramid_anchors(config.RPN_ANCHOR_SCALES,
                                             config.RPN_ANCHOR_RATIOS,
                                             backbone_shapes,
                                             config.BACKBONE_STRIDES,
                                             config.RPN_ANCHOR_STRIDE)

    # Keras requires a generator to run indefinately.
    while True:
        try:
            # Increment index to pick next image. Shuffle if at the start of an epoch.
            image_index = (image_index + 1) % len(image_ids)
            if shuffle and image_index == 0:
                np.random.shuffle(image_ids)

            # Get GT bounding boxes and masks for image.
            image_id = image_ids[image_index]
            image, image_meta, gt_class_ids, gt_boxes, gt_masks = \
                modellib.load_image_gt(dataset, config, image_id, augmentation=augmentation,
                              use_mini_mask=config.USE_MINI_MASK)
                
            
            # Replace class ids with foreground/background info if binary
            # class option is chosen
            # if binary_classes == True:
            #    gt_class_ids = np.minimum(gt_class_ids, 1)

            # Skip images that have no instances. This can happen in cases
            # where we train on a subset of classes and the image doesn't
            # have any of the classes we care about.
            if not np.any(gt_class_ids > 0):
                continue
                
#             print(gt_class_ids)

            # Use only positive class_ids
            categories = np.unique(gt_class_ids)
            _idx = categories > 0
            categories = categories[_idx]
            # Use only active classes
            active_categories = []
            for c in categories:
                if any(c == config.ACTIVE_CLASSES):
                    active_categories.append(c)
            
            # Skiop image if it contains no instance of any active class    
            if not np.any(np.array(active_categories) > 0):
                continue
            # Randomly select category
            category = np.random.choice(active_categories)
                
            # Generate siamese target crop
            target = get_one_target(category, dataset, config, augmentation=augmentation)
#             print(target_class_id)
            target_class_id = category
            target_class_ids = np.array([target_class_id])
            
            idx = gt_class_ids == target_class_id
            siamese_class_ids = idx.astype('int8')
#             print(idx)
#             print(gt_boxes.shape, gt_masks.shape)
            siamese_class_ids = siamese_class_ids[idx]
            gt_class_ids = gt_class_ids[idx]
            gt_boxes = gt_boxes[idx,:]
            gt_masks = gt_masks[:,:,idx]
#             print(gt_boxes.shape, gt_masks.shape)

            # RPN Targets
            rpn_match, rpn_bbox = modellib.build_rpn_targets(image.shape, anchors,
                                                    gt_class_ids, gt_boxes, config)

            # Mask R-CNN Targets
            if random_rois:
                rpn_rois = modellib.generate_random_rois(
                    image.shape, random_rois, gt_class_ids, gt_boxes)
                if detection_targets:
                    rois, mrcnn_class_ids, mrcnn_bbox, mrcnn_mask =\
                        modellib.build_detection_targets(
                            rpn_rois, gt_class_ids, gt_boxes, gt_masks, config)

            # Init batch arrays
            if b == 0:
                batch_image_meta = np.zeros(
                    (batch_size,) + image_meta.shape, dtype=image_meta.dtype)
                batch_rpn_match = np.zeros(
                    [batch_size, anchors.shape[0], 1], dtype=rpn_match.dtype)
                batch_rpn_bbox = np.zeros(
                    [batch_size, config.RPN_TRAIN_ANCHORS_PER_IMAGE, 4], dtype=rpn_bbox.dtype)
                batch_images = np.zeros(
                    (batch_size,) + image.shape, dtype=np.float32)
                batch_gt_class_ids = np.zeros(
                    (batch_size, config.MAX_GT_INSTANCES), dtype=np.int32)
                batch_gt_boxes = np.zeros(
                    (batch_size, config.MAX_GT_INSTANCES, 4), dtype=np.int32)
                batch_targets = np.zeros(
                    (batch_size,) + target.shape, dtype=np.float32)
#                 batch_target_class_ids = np.zeros(
#                     (batch_size, config.MAX_TARGET_INSTANCES), dtype=np.int32)
                if config.USE_MINI_MASK:
                    batch_gt_masks = np.zeros((batch_size, config.MINI_MASK_SHAPE[0], config.MINI_MASK_SHAPE[1],
                                               config.MAX_GT_INSTANCES))
                else:
                    batch_gt_masks = np.zeros(
                        (batch_size, image.shape[0], image.shape[1], config.MAX_GT_INSTANCES))
                if random_rois:
                    batch_rpn_rois = np.zeros(
                        (batch_size, rpn_rois.shape[0], 4), dtype=rpn_rois.dtype)
                    if detection_targets:
                        batch_rois = np.zeros(
                            (batch_size,) + rois.shape, dtype=rois.dtype)
                        batch_mrcnn_class_ids = np.zeros(
                            (batch_size,) + mrcnn_class_ids.shape, dtype=mrcnn_class_ids.dtype)
                        batch_mrcnn_bbox = np.zeros(
                            (batch_size,) + mrcnn_bbox.shape, dtype=mrcnn_bbox.dtype)
                        batch_mrcnn_mask = np.zeros(
                            (batch_size,) + mrcnn_mask.shape, dtype=mrcnn_mask.dtype)

            # If more instances than fits in the array, sub-sample from them.
            if gt_boxes.shape[0] > config.MAX_GT_INSTANCES:
                ids = np.random.choice(
                    np.arange(gt_boxes.shape[0]), config.MAX_GT_INSTANCES, replace=False)
                gt_class_ids = gt_class_ids[ids]
                siamese_class_ids = siamese_class_ids[ids]
                gt_boxes = gt_boxes[ids]
                gt_masks = gt_masks[:, :, ids]
                

            # Add to batch
            batch_image_meta[b] = image_meta
            batch_rpn_match[b] = rpn_match[:, np.newaxis]
            batch_rpn_bbox[b] = rpn_bbox
            batch_images[b] = modellib.mold_image(image.astype(np.float32), config)
            batch_targets[b] = modellib.mold_image(target.astype(np.float32), config)
            batch_gt_class_ids[b, :siamese_class_ids.shape[0]] = siamese_class_ids
#             batch_target_class_ids[b, :target_class_ids.shape[0]] = target_class_ids
            batch_gt_boxes[b, :gt_boxes.shape[0]] = gt_boxes
            batch_gt_masks[b, :, :, :gt_masks.shape[-1]] = gt_masks
            if random_rois:
                batch_rpn_rois[b] = rpn_rois
                if detection_targets:
                    batch_rois[b] = rois
                    batch_mrcnn_class_ids[b] = mrcnn_class_ids
                    batch_mrcnn_bbox[b] = mrcnn_bbox
                    batch_mrcnn_mask[b] = mrcnn_mask
            b += 1

            # Batch full?
            if b >= batch_size:
                inputs = [batch_images, batch_image_meta, batch_targets, batch_rpn_match, batch_rpn_bbox,
                          batch_gt_class_ids, batch_gt_boxes, batch_gt_masks]
                outputs = []

                if random_rois:
                    inputs.extend([batch_rpn_rois])
                    if detection_targets:
                        inputs.extend([batch_rois])
                        # Keras requires that output and targets have the same number of dimensions
                        batch_mrcnn_class_ids = np.expand_dims(
                            batch_mrcnn_class_ids, -1)
                        outputs.extend(
                            [batch_mrcnn_class_ids, batch_mrcnn_bbox, batch_mrcnn_mask])

                yield inputs, outputs

                # start a new batch
                b = 0
        except (GeneratorExit, KeyboardInterrupt):
            raise
        except:
            # Log it and skip the image
            modellib.logging.exception("Error processing image {}".format(
                dataset.image_info[image_id]))
            error_count += 1
            if error_count > 5:
                raise
                
                
### Dataset Utils ###

class IndexedCocoDataset(coco.CocoDataset):
    
    def build_indices(self):

        self.image_category_index = IndexedCocoDataset._build_image_category_index(self)
        self.category_image_index = IndexedCocoDataset._build_category_image_index(self.image_category_index)

    def _build_image_category_index(dataset):

        image_category_index = []
        for im in range(len(dataset.image_info)):
            # List all classes in an image
            coco_class_ids = list(\
                                  np.unique(\
                                            [dataset.image_info[im]['annotations'][i]['category_id']\
                                             for i in range(len(dataset.image_info[im]['annotations']))]\
                                           )\
                                 )
            # Map 91 class IDs 81 to Mask-RCNN model type IDs
            class_ids = [dataset.map_source_class_id("coco.{}".format(coco_class_ids[k]))\
                         for k in range(len(coco_class_ids))]
            # Put list together
            image_category_index.append(class_ids)

        return image_category_index

    def _build_category_image_index(image_category_index):

        category_image_index = []
        # Loop through all 81 Mask-RCNN classes/categories
        for category in range(max(image_category_index)[0]+1):
            # Find all images corresponding to the selected class/category 
            images_per_category = np.where(\
                [any(image_category_index[i][j] == category\
                 for j in range(len(image_category_index[i])))\
                 for i in range(len(image_category_index))])[0]
            # Put list together
            category_image_index.append(images_per_category)

        return category_image_index
    
    
    
### Visualization ###

def display_siamese_instances(target, image, boxes, masks, class_ids,
                      scores=None, title="",
                      figsize=(16, 16), ax=None,
                      show_mask=True, show_bbox=True,
                      colors=None, captions=None):
    """
    boxes: [num_instance, (y1, x1, y2, x2, class_id)] in image coordinates.
    masks: [height, width, num_instances]
    class_ids: [num_instances]
    class_names: list of class names of the dataset
    scores: (optional) confidence scores for each box
    title: (optional) Figure title
    show_mask, show_bbox: To show masks and bounding boxes or not
    figsize: (optional) the size of the image
    colors: (optional) An array or colors to use with each object
    captions: (optional) A list of strings to use as captions for each object
    """
    # Number of instances
    N = boxes.shape[0]
    if not N:
        print("\n*** No instances to display *** \n")
    else:
        assert boxes.shape[0] == masks.shape[-1] == class_ids.shape[0]

    # If no axis is passed, create one and automatically call show()
    auto_show = False
    if not ax:
        from matplotlib.gridspec import GridSpec
        # Use GridSpec to show target smaller than image
        fig = plt.figure(figsize=figsize)
        gs = GridSpec(3, 3)
        ax = plt.subplot(gs[:, 1:])
        target_ax = plt.subplot(gs[1, 0])
        auto_show = True

    # Generate random colors
    colors = colors or visualize.random_colors(N)

    # Show area outside image boundaries.
    height, width = image.shape[:2]
    ax.set_ylim(height + 10, -10)
    ax.set_xlim(-10, width + 10)
    ax.axis('off')
    ax.set_title(title)
    
    target_height, target_width = target.shape[:2]
    target_ax.set_ylim(target_height + 10, -10)
    target_ax.set_xlim(-10, target_width + 10)
    target_ax.axis('off')
    target_ax.set_title('target')

    masked_image = image.astype(np.uint32).copy()
    for i in range(N):
        color = colors[i]

        # Bounding box
        if not np.any(boxes[i]):
            # Skip this instance. Has no bbox. Likely lost in image cropping.
            continue
        y1, x1, y2, x2 = boxes[i]
        if show_bbox:
            p = visualize.patches.Rectangle((x1, y1), x2 - x1, y2 - y1, linewidth=2,
                                alpha=0.7, linestyle="dashed",
                                edgecolor=color, facecolor='none')
            ax.add_patch(p)

        # Label
        if not captions:
            class_id = class_ids[i]
            score = scores[i] if scores is not None else None
            x = random.randint(x1, (x1 + x2) // 2)
            caption = "{:.3f}".format(score) if score else 'no score'
        else:
            caption = captions[i]
        ax.text(x1, y1 + 8, caption,
                color='w', size=11, backgroundcolor="none")

        # Mask
        mask = masks[:, :, i]
        if show_mask:
            masked_image = visualize.apply_mask(masked_image, mask, color)

        # Mask Polygon
        # Pad to ensure proper polygons for masks that touch image edges.
        padded_mask = np.zeros(
            (mask.shape[0] + 2, mask.shape[1] + 2), dtype=np.uint8)
        padded_mask[1:-1, 1:-1] = mask
        contours = visualize.find_contours(padded_mask, 0.5)
        for verts in contours:
            # Subtract the padding and flip (y, x) to (x, y)
            verts = np.fliplr(verts) - 1
            p = visualize.Polygon(verts, facecolor="none", edgecolor=color)
            ax.add_patch(p)
    ax.imshow(masked_image.astype(np.uint8))
    target_ax.imshow(target.astype(np.uint8))
    if auto_show:
        plt.show()
        
    return
   

### Evaluation ###
        
def find_correct_detections(class_gt_boxes, detected_boxes, threshold=0.5, verbose=0):

    correct_ious = np.zeros((class_gt_boxes.shape[0], detected_boxes.shape[0]))
    epsilon = 0.0001
    for i in range(class_gt_boxes.shape[0]):
        gty1, gtx1, gty2, gtx2 = class_gt_boxes[i,:]
        for j in range(detected_boxes.shape[0]):
            pry1, prx1, pry2, prx2 = detected_boxes[j,:]
            dy = np.maximum(np.min([gty2, pry2]) - np.max([gty1, pry1]), 0)
            dx = np.maximum(np.min([gtx2, prx2]) - np.max([gtx1, prx1]), 0)
            intersection = dx*dy
            union = (gty2-gty1)*(gtx2-gtx1) + (pry2-pry1)*(prx2-prx1) - intersection
            IoU = intersection / (union + epsilon)
            if IoU >= threshold:
                if verbose !=0:
                    print(i, j, IoU)
                correct_ious[i,j] = IoU
                
    return correct_ious

def find_correct_segmentations(class_gt_masks, predicted_masks, class_gt_detections, predicted_detections, threshold=0.5, verbose=0, epsilon=0.0001):
    detection_ious = find_correct_detections(class_gt_detections, predicted_detections, threshold=threshold, verbose=verbose)

    segmentation_ious = np.zeros_like(detection_ious)
    for i in range(class_gt_masks.shape[0]):
        for j in range(predicted_masks.shape[0]):
            if detection_ious[i,j] < threshold:
                continue
            else:
                gty1, gtx1, gty2, gtx2 = class_gt_detections[i,:]
                pry1, prx1, pry2, prx2 = predicted_detections[j,:]
                oy1, ox1, oy2, ox2 = find_overlap_coordinates(gty1, gtx1, gty2, gtx2, pry1, prx1, pry2, prx2)

                predicted_mask = predicted_masks[j]
                class_gt_mask = skt.resize(class_gt_masks[i], (gty2 - gty1, gtx2 - gtx1)) > 0.0

                predicted_mask_overlap = predicted_mask[oy1 : oy2, ox1 : ox2]
                class_gt_mask_overlap = class_gt_mask[(oy1 - gty1) : (oy2 - gty1), (ox1 - gtx1) : (ox2 - gtx1)] 

                intersection = np.sum(predicted_mask_overlap * class_gt_mask_overlap)
                union = np.sum(predicted_mask) - np.sum(predicted_mask_overlap) + \
                        np.sum(class_gt_mask) - np.sum(class_gt_mask_overlap) + \
                        np.sum(np.clip(predicted_mask_overlap + class_gt_mask_overlap, 0, 1))
                IoU = intersection / (union + epsilon)

                segmentation_ious[i, j] = IoU

    return segmentation_ious


def find_overlap_coordinates(gty1, gtx1, gty2, gtx2, pry1, prx1, pry2, prx2):
    ox1 = np.max([gtx1, prx1])
    ox2 = np.min([gtx2, prx2])
    oy1 = np.max([gty1, pry1])
    oy2 = np.min([gty2, pry2])

    return oy1, ox1, oy2, ox2

def assign_detections(correct_ious, threshold=0.5, return_index=False):
    # Greedy assignment from first to last gt instance (could be optimized!)
    best_matches_iou = []
    best_matches_index = []
    for i in range(correct_ious.shape[0]):
        if any(correct_ious[i,:] >= threshold):
            best_match = np.max(correct_ious[i,:])
            best_matches_iou.append(best_match)
            index = np.argmax(correct_ious[i,:])
            best_matches_index.append(index)
            correct_ious[:,index] = 0
            correct_ious[i,index] = best_match
        else:
            best_matches_iou.append(0)

            # If IoU is zero just append -1 as the best matching index
            # to mark this case and simplify the assign_segmentations
            best_matches_index.append(-1)
            
    if return_index:
        return best_matches_iou, best_matches_index
    else:
        return best_matches_iou

def assign_segmentations(correct_ious_segmentation, correct_ious_detection, threshold=0.5):
    _, best_matches_index = assign_detections(correct_ious_detection, threshold=threshold, return_index=True)

    best_matches_iou = []
    for i in range(correct_ious_segmentation.shape[0]):
        best_index_i = best_matches_index[i]
        if best_index_i == -1:
            best_matches_iou.append(0)
            continue
        best_matches_iou.append(correct_ious_segmentation[i][best_index_i])
            
    return best_matches_iou

