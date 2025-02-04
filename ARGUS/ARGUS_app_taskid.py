import os

from ARGUS_taskid_inference import ARGUS_taskid_inference

class ARGUS_app_taskid:
    def __init__(self, argus_dir=".", device_num=0, source=None):
        self.taskid = ARGUS_taskid_inference(
            config_file_name=os.path.join(argus_dir, "ARGUS_taskid.cfg"),
            network_name="final",
            device_num=device_num,
            source=source
        )
        taskid_best_models = [0]
        for r in range(self.taskid.num_models):
            model_name = os.path.join(
                argus_dir,
                "Models",
                "taskid_run"+str(r),
                "best_model_"+str(taskid_best_models[r])+".pth"
            )
            self.taskid.load_model(r, model_name)
            
        self.result = 0
        self.confidence = [0, 0, 0, 0]
            
    def preprocess(self, vid_img):
        self.taskid.preprocess(
            vid_img,
            lbl=None,
            slice_num=None,
            scale_data=True,
            rotate_data=False)
        
    def inference(self):
        self.result, self.confidence = self.taskid.inference()
        
    def decision(self):
        return self.result, self.confidence
        
