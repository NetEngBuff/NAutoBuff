import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'NSOT', 'python-files'))
from gnmi_hosts import update_gnmic_yaml_from_hosts
update_gnmic_yaml_from_hosts()
print('Done')
