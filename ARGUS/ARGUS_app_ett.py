import os

from ARGUS_ett_roi_inference import ARGUS_ett_roi_inference

class ARGUS_app_ett:
    def __init__(self, argus_dir=".", device_num=None, source=None):
        self.ett_roi = ARGUS_ett_roi_inference(
            config_file_name=os.path.join(argus_dir, "ARGUS_ett_roi.cfg"),
            network_name="vfold",
            device_num=device_num,
            source=source
        )
        
        ett_roi_best_models = [9, 2, 0]
        for r in range(self.ett_roi.num_models):
            model_name = os.path.join(
                argus_dir,
                "Models",
                "ett_run"+str(r),
                "best_model_"+str(ett_roi_best_models[r])+".pth"
            )
            self.ett_roi.load_model(r, model_name)

        self.labels = None

        self.result = 0
        self.confidence = [0, 0]
            
    def roi_preprocess(self, vid_img):
        self.ett_roi.volume_preprocess(vid_img)
        
    def roi_inference(self):
        self.result, self.confidence = self.ett_roi.volume_inference()
        
    def decision(self):
        return self.result, self.confidence

    def gradcam(self, runs=None, slice_num=None):
        if slice_num != None:
            if slice_num < 0:
                slice_num = self.ett_roi.input_image.GetLargestPossibleRegion().GetSize()[2] + slice_num
            self.ett_roi.volume_inference(slice_min=slice_num, slice_max=slice_num+1, step=1)
        else:
            self.result, self.confidence = self.ett_roi.volume_inference()
        return self.ett_roi.gradcam(runs)
    
    def occlusion_sensitivity(self, runs=None, slice_num=None, mask_size=16):
        if slice_num != None:
            if slice_num < 0:
                slice_num = self.ett_roi.input_image.GetLargestPossibleRegion().GetSize()[2] + slice_num
            self.ett_roi.volume_inference(slice_min=slice_num, slice_max=slice_num+1, step=1)
        else:
            self.result, self.confidence = self.ett_roi.volume_inference()
        return self.ett_roi.occlusion_sensitivity(runs, mask_size=mask_size)
    
    