import pyVmomi
import time
import datetime
from common.methods import set_progress
from resourcehandlers.vmware.pyvmomi_wrapper import get_vm_by_uuid
from resourcehandlers.vmware.models import VsphereResourceHandler
from resourcehandlers.vmware.vmware_41 import TechnologyWrapper
from jobengine.jobmodules.syncvmsjob import SyncVMsClass


def get_vmware_service_instance(vcenter_rh):
    """
    :return: the pyvmomi service instance object that represents a connection to vCenter,
    and which can be used for making API calls.
    """
    assert isinstance(vcenter_rh, VsphereResourceHandler)
    vcenter_rh.init()
    wc = vcenter_rh.resource_technology.work_class
    assert isinstance(wc, TechnologyWrapper)
    return wc._get_connection()


def check_task(task_id):
    while True:
        task_info = task_id.info
        state = task_info.state
        if state == "running":
            progress = task_info.progress or 0
            set_progress('VM clone task is {}% complete'.format(progress))
            time.sleep(3)
        elif state == "success":
            if task_info.result:
                progress = task_id.info.progress
                set_progress('VM clone task is 100% complete')
                if hasattr(task_info.result, 'config'):
                    uuid = task_info.result.config.uuid
            break
    return uuid


def run(job, logger=None, **kwargs):
    server = job.server_set.first()
    rh = server.resource_handler.cast()
    group = server.group
    env = server.environment
    owner = server.owner
    new_name = str('{{ clone_name }}')
    do_linked_clone = {{ linked_clone }}

    # Connect to RH
    si = get_vmware_service_instance(rh)
    vm = get_vm_by_uuid(si, server.resource_handler_svr_id)
    assert isinstance(vm, pyVmomi.vim.VirtualMachine)

    # Define the location, empty defaults to same location as the source vm
    set_progress("Generating VMware Clone Config")
    relocate_spec = pyVmomi.vim.vm.RelocateSpec()

    # Linked clone
    if do_linked_clone:
        set_progress('Clones as "Linked Clone"')
        relocate_spec.diskMoveType = 'createNewChildDiskBacking'

    # Define the clone config specs
    cloneSpec = pyVmomi.vim.vm.CloneSpec(
        powerOn=False,
        template=False,
        location=relocate_spec)

    # Clone the Virtual Machine with provided specs
    set_progress("Cloning {} to {}".format(server.hostname, new_name))
    clone_task = vm.Clone(name=new_name, folder=vm.parent, spec=cloneSpec)

    # TODO Possibly replace the sync vm with create CB server object

    # Wait for completion and get the new vm uuid
    uuid = check_task(clone_task)

    # Set the new vm annotation
    set_progress("Updating new virtual machine annotation")
    clone_add_date = datetime.datetime.now()
    annotation = 'Cloned by {} using CloudBolt on {} [Job ID={}]'.format(
        server.owner, clone_add_date, job.id)
    new_vm = get_vm_by_uuid(si, uuid)
    assert isinstance(new_vm, pyVmomi.vim.VirtualMachine)
    configSpec = pyVmomi.vim.vm.ConfigSpec()
    configSpec.annotation = annotation
    new_vm.ReconfigVM_Task(configSpec)

    # Sync the cloned VM to CloudBolt
    vm = {}
    vm['hostname'] = new_name
    vm['uuid'] = uuid
    vm['power_status'] = 'POWEROFF'
    sync_class = SyncVMsClass()
    sync_class.import_vm(vm, rh, group, env, owner)

    return "", "", ""


if __name__ == '__main__':
    import os
    import sys

    localpath = os.path.join('var', 'opt', 'cloudbolt')
    sys.path.append(localpath)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'
    job_id = sys.argv[1]
    print run(job=job_id)