import site
site.addsitedir("../ARGUS")

from ARGUS_classification_train import ARGUS_classification_train

class ARGUS_ett_roi_train(ARGUS_classification_train):
    def __init__(self, config_file_name="../ARGUS/ARGUS_ett_roi.cfg", network_name="vfold", device_num=0):
        super().__init__(config_file_name, network_name, device_num)
