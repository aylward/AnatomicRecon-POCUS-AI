import warnings

warnings.filterwarnings("ignore")

from monai.utils import first, set_determinism
from monai.transforms import (
    AddChanneld,
    AsChannelFirstd,
    AsDiscrete,
    AsDiscreted,
    Compose,
    EnsureType,
    Invertd,
    LabelFilterd,
    Lambdad,
    LoadImaged,
    RandFlipd,
    RandSpatialCropd,
    RandZoomd,
    Resized,
    ScaleIntensityRanged,
    SpatialResampled,
    SpatialCrop,
    SpatialCropd,
    ToTensord,
)
from monai.handlers.utils import from_engine
from monai.networks.nets import UNet
from monai.networks.layers import Norm
from monai.metrics import DiceMetric
from monai.losses import DiceLoss
from monai.inferers import sliding_window_inference
from monai.data import PersistentDataset, CacheDataset, DataLoader, Dataset, decollate_batch, list_data_collate
from monai.config import print_config
from monai.apps import download_and_extract
import torch

import os
from glob import glob
import ubelt as ub
import pathlib

import numpy as np
import matplotlib.pyplot as plt
from scipy.ndimage import rotate

import itk
from itk import TubeTK as tube

import site
site.addsitedir("../../../ARGUS")

from ARGUS_Transforms import ARGUS_RandSpatialCropSlicesd

import pint
Ureg = pint.UnitRegistry()

def use_needle_label_only(img):
    return np.where(img==2,1,0)

class ARGUS_Needle_Network:
    def __init__(self):
        self.filename_base = "ARUNet-NeedleArtery-VFold-Training"

        self.num_classes = 2
        self.class_blur = [2, 1]
        self.class_min_size = [0, 100]
        self.class_max_size = [0, 5000]
        self.class_morph = [0, 1]
        self.class_keep_only_largest=[False, False]

        self.max_epochs = 2500

        self.num_folds = 10

        self.net_dims = 2

        # Mean, Std, RawFrame, gradx, grady, gradz
        self.net_in_channels = 6

        self.net_channels = (16, 32, 64, 128, 32)
        self.net_strides = (2, 2, 2, 2)

        self.cache_rate_train = 1
        self.num_workers_train = 4
        self.batch_size_train = 2

        self.val_interval = 10
        self.cache_rate_val = 1
        self.num_workers_val = 4
        self.batch_size_val = 2

        self.cache_rate_test = 0
        self.num_workers_test = 1
        self.batch_size_test = 1

        self.num_slices = 16

        self.size_x = 320
        self.size_y = 480

        self.all_train_images = []
        self.all_train_labels = []

        self.train_transforms = Compose(
            [
                LoadImaged(keys=["image", "label"]),
                AsChannelFirstd(keys="image"),
                AsChannelFirstd(keys="label"),
                Lambdad(
                    func=use_needle_label_only,
                    keys='label',
                ),
                ARGUS_RandSpatialCropSlicesd(
                    num_slices=[self.num_slices, 1],
                    axis=0,
                    reduce_to_statistics=[True, False],
                    require_labeled=True,
                    extended=False,
                    include_center_slice=True,
                    include_gradient=True,
                    keys=["image", "label"],
                ),
                RandFlipd(prob=0.5, spatial_axis=0, keys=["image", "label"]),
                ToTensord(keys=["image", "label"], dtype=torch.float),
            ]
        )

        self.val_transforms = Compose(
            [
                LoadImaged(keys=["image", "label"]),
                AsChannelFirstd(keys="image"),
                AsChannelFirstd(keys="label"),
                Lambdad(
                    func=use_needle_label_only,
                    keys='label',
                ),
                ARGUS_RandSpatialCropSlicesd(
                    num_slices=[self.num_slices, 1],
                    center_slice=-self.num_slices / 2 - 1,
                    axis=0,
                    reduce_to_statistics=[True, False],
                    extended=False,
                    include_center_slice=True,
                    include_gradient=True,
                    keys=["image", "label"],
                ),
                ToTensord(keys=["image", "label"], dtype=torch.float),
            ]
        )

        self.test_transforms = Compose(
            [
                LoadImaged(keys=["image", "label"]),
                AsChannelFirstd(keys="image"),
                AsChannelFirstd(keys="label"),
                Lambdad(
                    func=use_needle_label_only,
                    keys='label',
                ),
                ARGUS_RandSpatialCropSlicesd(
                    num_slices=[self.num_slices, 1],
                    center_slice=-self.num_slices / 2 - 1,
                    axis=0,
                    reduce_to_statistics=[True, False],
                    extended=False,
                    include_center_slice=True,
                    include_gradient=True,
                    keys=["image", "label"],
                ),
                ToTensord(keys=["image", "label"], dtype=torch.float),
            ]
        )

    def setup_vfold_files(self, img_dir, anno_dir):
        self.all_train_images = sorted(glob(os.path.join(img_dir, "*_cropM_prep.nii.gz")))
        self.all_train_labels = sorted(glob(os.path.join(anno_dir, "*.overlay_prep.nii.gz")))

        total_bytes = 0
        for p in self.all_train_images:
            p = ub.Path(p)
            total_bytes += p.stat().st_size
        print((total_bytes * Ureg.byte).to("GiB"))

        total_bytes = 0
        for p in self.all_train_labels:
            p = ub.Path(p)
            total_bytes += p.stat().st_size
        print((total_bytes * Ureg.byte).to("GiB"))

        num_images = len(self.all_train_images)
        print("Num images / labels =", num_images, len(self.all_train_labels))

        # 46 ok
        # 178 bad
        # 207 ok
        # 230 ok
        # 54 ok
        p_prefix = [
            " 11",
            " 46",
            " 207",
            " 67",
            " 93",
            " 94",
            " 134",
            " 211",
            " 222A",
            " 153",
            " 240",
            " 193",
        ]
        n_prefix = [
            " 57",
            " 136",
            " 179",
            " 189",
            " 204",
            " 205",
            " 217",
            " 238",
            " 39",
            " 230",
            " 54",
            " 191",
        ]

        fold_prefix_list = []
        p_count = 0
        n_count = 0
        for i in range(self.num_folds):
            num_p = 1
            num_n = 1
            if i > self.num_folds - 5:
                if i % 2 == 0:
                    num_p = 2
                    num_n = 1
                else:
                    num_p = 1
                    num_n = 2
            f = []
            if p_count < len(p_prefix):
                for p in range(num_p):
                    f.append([p_prefix[p_count + p]])
            p_count += num_p
            if n_count < len(n_prefix):
                for n in range(num_n):
                    f.append([n_prefix[n_count + n]])
            n_count += num_n
            fold_prefix_list.append(f)

        for i in range(self.num_folds):
            print(i, fold_prefix_list[i])

        self.train_files = []
        self.val_files = []
        self.test_files = []

        for i in range(self.num_folds):
            tr_folds = []
            va_folds = []
            for f in range(i, i + self.num_folds - 3):
                tr_folds.append(fold_prefix_list[f % self.num_folds])
            tr_folds = list(np.concatenate(tr_folds).flat)
            for f in range(i + self.num_folds - 3, i + self.num_folds - 1):
                va_folds.append(fold_prefix_list[f % self.num_folds])
            va_folds = list(np.concatenate(va_folds).flat)
            te_folds = list(
                np.concatenate(
                    fold_prefix_list[(i + self.num_folds - 1) % self.num_folds]
                ).flat
            )
            self.train_files.append(
                [
                    {"image": img, "label": seg}
                    for img, seg in zip(
                        [
                            im
                            for im in self.all_train_images
                            if any(pref in im for pref in tr_folds)
                        ],
                        [
                            se
                            for se in self.all_train_labels
                            if any(pref in se for pref in tr_folds)
                        ],
                    )
                ]
            )
            self.val_files.append(
                [
                    {"image": img, "label": seg}
                    for img, seg in zip(
                        [
                            im
                            for im in self.all_train_images
                            if any(pref in im for pref in va_folds)
                        ],
                        [
                            se
                            for se in self.all_train_labels
                            if any(pref in se for pref in va_folds)
                        ],
                    )
                ]
            )
            self.test_files.append(
                [
                    {"image": img, "label": seg}
                    for img, seg in zip(
                        [
                            im
                            for im in self.all_train_images
                            if any(pref in im for pref in te_folds)
                        ],
                        [
                            se
                            for se in self.all_train_labels
                            if any(pref in se for pref in te_folds)
                        ],
                    )
                ]
            )
            print(
                len(self.train_files[i]),
                len(self.val_files[i]),
                len(self.test_files[i]),
            )

    def setup_training_vfold(self, vfold_num):
        persistent_cache = pathlib.Path("./data_cache"+str(vfold_num), "persistent_cache")
        persistent_cache.mkdir(parents=True, exist_ok=True)
        
        self.vfold_num = vfold_num

        train_ds = PersistentDataset(
            data=self.train_files[self.vfold_num],
            transform=self.train_transforms,
            cache_dir=persistent_cache,
            #cache_rate=self.cache_rate_train,
            #num_workers=self.num_workers_train
        )
        self.train_loader = DataLoader(
            train_ds,
            batch_size=self.batch_size_train,
            shuffle=True,
            num_workers=self.num_workers_train,
            collate_fn=list_data_collate,
            pin_memory=True,
        )

        val_ds = PersistentDataset(
            data=self.val_files[self.vfold_num],
            transform=self.val_transforms,
            cache_dir=persistent_cache,
            #cache_rate=self.cache_rate_val,
            #num_workers=self.num_workers_val

        )
        self.val_loader = DataLoader(
            val_ds, 
            batch_size=self.batch_size_val, 
            num_workers=self.num_workers_val,
            collate_fn=list_data_collate,
            pin_memory=True,
        )

    def setup_testing_vfold(self, vfold_num):
        persistent_cache = pathlib.Path("./data_cache"+str(vfold_num), "persistent_cache")
        persistent_cache.mkdir(parents=True, exist_ok=True)
        
        self.vfold_num = vfold_num
        test_ds = PersistentDataset(
            data=self.test_files[self.vfold_num],
            transform=self.test_transforms,
            cache_dir=persistent_cache,
            #cache_rate=self.cache_rate_test,
            #num_workers=self.num_workers_test
        )
        self.test_loader = DataLoader(
            test_ds, 
            batch_size=self.batch_size_test, 
            num_workers=self.num_workers_test,
            collate_fn=list_data_collate,
            pin_memory=True,
        )

    def train_vfold(self, run_id=0, device_num=0):
        model_filename_base = (
            "./Results/"
            + self.filename_base
            + "-"
            + str(self.num_slices)
            + "s-VFold-Run"
            + str(run_id)
        )
        if not os.path.exists(model_filename_base):
            os.makedirs(model_filename_base)
        model_filename_base = model_filename_base + "/"

        device = torch.device("cuda:" + str(device_num))

        model = UNet(
            dimensions=self.net_dims,
            in_channels=self.net_in_channels,
            out_channels=self.num_classes,
            channels=self.net_channels,
            strides=self.net_strides,
            num_res_units=2,
            norm=Norm.BATCH,
        ).to(device)
        loss_function = DiceLoss(to_onehot_y=True, softmax=True)
        optimizer = torch.optim.Adam(model.parameters(), 1e-4)
        dice_metric = DiceMetric(include_background=False, reduction="mean")

        post_pred = Compose(
            [
                EnsureType(),
                AsDiscrete(argmax=True, to_onehot=True, num_classes=self.num_classes),
            ]
        )
        post_label = Compose(
            [EnsureType(), AsDiscrete(to_onehot=True, num_classes=self.num_classes)]
        )

        best_metric = -1
        best_metric_epoch = -1
        epoch_loss_values = []
        metric_values = []

        for epoch in range(self.max_epochs):
            print("-" * 10)
            print(f"{self.vfold_num}: epoch {epoch + 1}/{self.max_epochs}",flush=True)
            model.train()
            epoch_loss = 0
            for step,batch_data in enumerate(self.train_loader):
                inputs = batch_data["image"].to(device)
                labels = batch_data["label"].to(device)
                optimizer.zero_grad()
                outputs = model(inputs)
                loss = loss_function(outputs, labels)
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item()
                print(
                    f"{step} /"
                    f" {len(self.train_files[self.vfold_num])//self.train_loader.batch_size},"
                    f" train_loss: {loss.item():.4f}", flush=True
                )
            epoch_loss /= len(self.train_loader)
            epoch_loss_values.append(epoch_loss)
            print(
                f"{self.vfold_num} epoch {epoch+1}" f" average loss: {epoch_loss:.4f}",
                flush=True,
            )

            if (epoch + 1) % self.val_interval == 0:
                model.eval()
                with torch.no_grad():
                    for val_data in self.val_loader:
                        val_inputs, val_labels = (val_data["image"], val_data["label"])
                        val_inputs = val_inputs.to(device)
                        val_labels = val_labels.to(device)
                        roi_size = (self.size_x, self.size_y)
                        val_outputs = sliding_window_inference(
                            val_inputs, roi_size, self.batch_size_val, model
                        )
                        # val_outputs = model(val_inputs)
                        val_outputs = [
                            post_pred(i) for i in decollate_batch(val_outputs)
                        ]
                        val_labels = [
                            post_label(i) for i in decollate_batch(val_labels)
                        ]
                        # compute metric for current iteration
                        dice_metric(y_pred=val_outputs, y=val_labels)

                    # aggregate the final mean dice result
                    metric = dice_metric.aggregate().item()
                    # reset the status for next validation round
                    dice_metric.reset()

                    metric_values.append(metric)
                    if epoch > 100:
                        metric = np.mean(metric_values[-self.val_interval:-1])
                        if metric > best_metric:
                            best_metric = metric
                            best_metric_epoch = epoch + 1
                            torch.save(
                                model.state_dict(),
                                model_filename_base
                                + "best_model.vfold_"
                                + str(self.vfold_num)
                                + ".pth",
                            )
                            print("saved new best metric model")
                    print(
                        f"Current epoch: {epoch + 1}"
                        f" current mean dice: {metric:.4f}"
                    )
                    print(
                        f"Best mean dice: {best_metric:.4f}"
                        f" at epoch: {best_metric_epoch}"
                    )
                    torch.save(
                        model.state_dict(),
                        model_filename_base
                        + "last_model.vfold_"
                        + str(self.vfold_num)
                        + ".pth",
                    )
                    np.save(
                        model_filename_base + "loss_" + str(self.vfold_num) + ".npy",
                        epoch_loss_values,
                    )
                    np.save(
                        model_filename_base
                        + "val_dice_"
                        + str(self.vfold_num)
                        + ".npy",
                        metric_values,
                    )

    def train_run(self, run_id=0, device=0):
        for i in range(0, self.num_folds):
            self.setup_training_vfold(i)
            self.train_vfold(run_id, device)

    def test_vfold(self, model_type="best", run_id=0, device_num=0):
        model_filename_base = (
            "./Results/"
            + self.filename_base
            + "-"
            + str(self.num_slices)
            + "s-VFold-Run"
            + str(run_id)
            + "/"
        )

        model_file = (
            model_filename_base
            + model_type
            + "_model.vfold_"
            + str(self.vfold_num)
            + ".pth"
        )

        device = torch.device("cuda:" + str(device_num))

        test_outputs_total = []
        test_images_total = []
        test_labels_total = []

        if os.path.exists(model_file):
            model = UNet(
                dimensions=self.net_dims,
                in_channels=self.net_in_channels,
                out_channels=self.num_classes,
                channels=self.net_channels,
                strides=self.net_strides,
                num_res_units=2,
                norm=Norm.BATCH,
            ).to(device)

            model.load_state_dict(torch.load(model_file, map_location=device))
            model.eval()

            with torch.no_grad():
                for b, test_data in enumerate(self.test_loader):
                    roi_size = (self.size_x, self.size_y)
                    test_outputs = sliding_window_inference(
                        test_data["image"].to(device),
                        roi_size,
                        self.batch_size_test,
                        model,
                    ).cpu()
                    if b == 0:
                        test_outputs_total = test_outputs
                        test_images_total = test_data["image"]
                        test_labels_total = test_data["label"]
                    else:
                        test_outputs_total = np.concatenate(
                            (test_outputs_total, test_outputs), axis=0
                        )
                        test_images_total = np.concatenate(
                            (test_images_total, test_data["image"]), axis=0
                        )
                        test_labels_total = np.concatenate(
                            (test_labels_total, test_data["label"]), axis=0
                        )
        else:
            print("ERROR: Model file not found:", model_file, "!!")

        return test_outputs_total, test_images_total, test_labels_total

    def view_training_image(self, image_num=0):
        img_name = self.all_train_images[image_num]
        print(img_name)
        img = itk.imread(img_name)
        lbl = itk.imread(self.all_train_labels[image_num])
        num_plots = 5
        num_slices = img.shape[0]
        step_slices = num_slices / num_plots
        plt.figure(figsize=[20, 10])
        for s in range(num_plots):
            slice_num = int(step_slices * s)
            plt.subplot(2, num_plots, s + 1)
            plt.axis('off')
            plt.imshow(img[slice_num, :, :])
            plt.subplot(2, num_plots, num_plots + s + 1)
            plt.axis('off')
            plt.imshow(lbl[slice_num, :, :])

    def view_training_vfold_batch(self, batch_num=0):
        with torch.no_grad():
            for count, batch_data in enumerate(self.train_loader):
                if count == batch_num:
                    inputs, labels = (batch_data["image"], batch_data["label"])
                    num_images = inputs.shape[0]
                    plt.figure(figsize=[30, 30])
                    for i in range(num_images):
                        img = inputs[i]
                        lbl = labels[i]
                        num_channels = img.shape[0]
                        for c in range(num_channels):
                            plt.subplot(
                                num_images,
                                num_channels + 1,
                                i * (num_channels + 1) + c + 1,
                            )
                            plt.axis('off')
                            plt.imshow(rotate(img[c, :, :],270))
                        plt.subplot(
                            num_images,
                            num_channels + 1,
                            i * (num_channels + 1) + num_channels + 1,
                        )
                        plt.axis('off')
                        plt.imshow(rotate(lbl[0, :, :],270))
                    break

    def view_metric_curves(self, vfold_num, run_id=0):
        model_filename_base = (
            "./Results/"
            + self.filename_base
            + "-"
            + str(self.num_slices)
            + "s-VFold-Run"
            + str(run_id)
            + "/"
        )
        loss_file = model_filename_base + "loss_" + str(vfold_num) + ".npy"
        if os.path.exists(loss_file):
            epoch_loss_values = np.load(loss_file)

            metric_file = model_filename_base + "val_dice_" + str(vfold_num) + ".npy"
            metric_values = np.load(metric_file)

            plt.figure("Train", (12, 6))

            plt.subplot(1, 2, 1)
            plt.title("Epoch Average Loss")
            x = [i + 1 for i in range(len(epoch_loss_values))]
            y = epoch_loss_values
            plt.xlabel("epoch")
            plt.plot(x, y)
            plt.ylim([0.0, 0.8])

            plt.subplot(1, 2, 2)
            plt.title("Val Mean Dice")
            x = [2 * (i + 1) for i in range(len(metric_values))]
            y = metric_values
            plt.xlabel("epoch")
            plt.plot(x, y)
            plt.ylim([0.0, 0.8])

            plt.show()

    def view_testing_results_vfold(self, model_type="best", run_id=[0], device_num=0):
        print("VFOLD =", self.vfold_num, "of", self.num_folds - 1)

        test_outputs = []
        test_images = []
        test_labels = []
        for r in run_id:
            outs, imgs, lbls = self.test_vfold(model_type, r, device_num)
            test_outputs.append(outs)
            test_images = imgs
            test_labels = lbls

        num_runs = len(run_id)

        num_subplots = max(
                self.net_in_channels + 1,
                self.num_classes + self.num_classes + 2
        )
        
        for image_num in range(len(test_images)):
            fname = os.path.basename(
                self.test_files[self.vfold_num][image_num]["image"]
            )
            print("Image:", fname)

            plt.figure("Testing", (30,20))
            subplot_num = 1
            for c in range(self.net_in_channels):
                plt.subplot(num_runs+1, num_subplots, subplot_num)
                plt.title(f"F"+str(c))
                tmpV = test_images[image_num, c, :, :]
                plt.axis('off')
                plt.imshow(rotate(tmpV,270), cmap="gray")
                subplot_num += 1
            plt.subplot(num_runs+1, num_subplots, num_subplots)
            plt.title(f"L")
            tmpV = test_labels[image_num, 0, :, :]
            for c in range(self.num_classes):
                tmpV[0, c] = c
            plt.axis('off')
            plt.imshow(rotate(tmpV,270))
            subplot_num += 1
 
            # Indent by one plot
            prob_shape = test_outputs[0][0].shape
            prob_total = np.zeros(prob_shape)
            for run_num in range(num_runs):
                prob = np.empty(prob_shape)
                run_output = test_outputs[run_num][image_num]
                for c in range(self.num_classes):
                    itkProb = itk.GetImageFromArray(run_output[c])
                    imMathProb = tube.ImageMath.New(itkProb)
                    imMathProb.Blur(self.class_blur[c])
                    itkProb = imMathProb.GetOutput()
                    prob[c] = itk.GetArrayFromImage(itkProb)
                pmin = prob.min()
                pmax = prob.max()
                prange = pmax - pmin
                prob = (prob - pmin) / prange
                for c in range(1,self.num_classes):
                    class_array = np.argmax(prob, axis=0)
                    count = np.count_nonzero(class_array == c)
                    done = False
                    while not done:
                        done = True
                        while count < self.class_min_size[c]:
                            prob[c] = prob[c] * 1.05
                            class_array = np.argmax(prob, axis=0)
                            count = np.count_nonzero(class_array == c)
                            done = False
                        while count > self.class_max_size[c]:
                            prob[c] = prob[c] * 0.95
                            class_array = np.argmax(prob, axis=0)
                            count = np.count_nonzero(class_array == c)
                            done = False
                denom = np.sum(prob, axis=0)
                denom = np.where(denom == 0, 1, denom)
                prob =  prob / denom
                prob_total += prob
                subplot_num = num_subplots*(run_num+1) + 2
                for c in range(self.num_classes):
                    plt.subplot(num_runs+1, num_subplots, subplot_num)
                    plt.title(f"R" + str(run_num) + " C" + str(c))
                    tmpV = prob[c]
                    plt.axis('off')
                    plt.imshow(rotate(tmpV,270), cmap="gray")
                    subplot_num += 1

            prob_total = prob_total / num_runs
            subplot_num = (num_runs+1)*num_subplots - self.num_classes
        
            for c in range(self.num_classes):
                plt.subplot(num_runs+1, num_subplots, subplot_num)
                plt.title(f"E"+str(c))
                tmpV = prob_total[c]
                plt.axis('off')
                plt.imshow(rotate(tmpV,270), cmap="gray")
                subplot_num += 1
 
            class_array = np.argmax(prob_total, axis=0)
            class_image = itk.GetImageFromArray(class_array.astype(np.float32))
                
            for c in range(1,self.num_classes):
                imMathClassCleanup = tube.ImageMath.New(class_image)
                imMathClassCleanup.Dilate(self.class_morph[c], c, 0)
                imMathClassCleanup.Erode(self.class_morph[c], c, 0)
                imMathClassCleanup.Threshold(c, c, 1, 0)
                class_clean_image = imMathClassCleanup.GetOutputUChar()
 
                if self.class_keep_only_largest[c]:
                    seg = itk.itkARGUS.SegmentConnectedComponents.New(
                        Input=class_clean_image
                    )
                    seg.SetKeepOnlyLargestComponent(True)
                    seg.Update()
                    class_clean_image = seg.GetOutput()
                    
                class_clean_array = itk.GetArrayFromImage(class_clean_image)
                class_array = np.where(class_array == c, 0, class_array)
                class_array = np.where(class_clean_array != 0, c, class_array)
                
            plt.subplot(num_runs+1, num_subplots, subplot_num)
            plt.title(f"Label")
            tmpV = class_array
            for c in range(self.num_classes):
                tmpV[0, c] = c
            plt.axis('off')
            plt.imshow(rotate(tmpV,270))
            plt.show()
            
            class_image = itk.GetImageFromArray(class_array.astype(np.float32))
            fname = "vf"+str(self.vfold_num)+"r"+str(run_num)+".mha"
            itk.imwrite(class_image, fname)
