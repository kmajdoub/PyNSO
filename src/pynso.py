"""
@author: Majdoub khalid
This is an nso A-Z test automation framework
It will make creating a CI-CD for your NSO services just a piece of cake
All in one python library
"""
import difflib
import logging
import os
import subprocess
import ncs
from .pynso_exceptions import *
import re
import time

# m = ncs.maapi.Maapi()
# ncs.maapi.Session(m, 'admin', 'admin')
# t = m.start_write_trans()


def get_log():
    format = "<%(asctime)-15s %(levelname)s line %(lineno)d> %(filename)s %(funcName)s: - %(message)s"
    test_bed = os.path.dirname(os.path.realpath(__file__))
    log_path = os.path.join(test_bed, "../pynso.log")
    logging.basicConfig(filename=log_path, level=logging.INFO, format=format)
    log = logging.getLogger("pynso_logger")
    return log

MAX_RETRIES = 3

def key_path(xpath):
    res = xpath
    res = res.replace('[', '{').replace(']', '}').replace("'", '')
    regex = "([-|\w]+[:|=])"
    for match in re.findall(regex, res):
        res = res.replace(match, '')
    return res

def retry(fn):
    def wrapped(self, *args, **kwargs):
        for counter in range(MAX_RETRIES):
            try:
                result = fn(self, *args, **kwargs)
                break
            except NoSPRegistrationError as e:
                if counter == MAX_RETRIES - 1:
                    raise
                self.log.info(f"No register error catched. Retrying after 1 seconds ...")
                time.sleep(1)
            except StillInZombieStateError as e:
                if counter == MAX_RETRIES - 1:
                    raise
                self.log.info(f"In zombie state catched.")
                regex = """in\s+zombie\s+state\s+:\s+\'(.*)\'"""
                service_xpath = re.findall(regex, str(e))[0]
                service = self.root().zombies.service[service_xpath]
                regex2 = "\[etr-id='(\S+)'\]"
                etr_id = re.findall(regex2, service_xpath)[0]
                self.log.info(f"etr id: {etr_id}")
                self.sync_from(etr_id)
                output = service.resurrect()
                time.sleep(1)
                self.log.info(f"Zombie {service_xpath} resurrection {output.result}")
                try:
                    t, root = self.open_transaction('w')
                    t.delete(key_path(service_xpath))
                    t.apply()
                    self.log.info(f"{service_xpath} deleted ok")
                except Exception as e:
                    pass
            except OutOfSyncError as e:
                self.log.info("Out of sync error catched. Syncing from device ...")
                regex = "device (\S+): out of sync"
                output = re.findall(regex, str(e))
                self.sync_from(output[0])
        return result
    return wrapped

class PyNSO:

    def __init__(self, username='admin', password='admin', log=None, NCS_RUN_DIR=None, NETSIM_DIR=None):
        if not NCS_RUN_DIR:
            self.NCS_RUN_DIR = "~/ncs-run"
        else:
            self.NCS_RUN_DIR = NCS_RUN_DIR
        if not NETSIM_DIR:
            self.NETSIM_DIR = f"{NCS_RUN_DIR}/packages"
        else:
            self.NETSIM_DIR = NETSIM_DIR
        self.username = username
        self.password = password
        self.open_session()
        if not log:
            self.log = get_log()
        else:
            self.log = log

    def set_debug(self):
        self.log.level = logging.DEBUG

    def root(self):
        """
        Get self maapi session root
        :return:
        """
        return ncs.maagic.get_root(self.session)

    def open_session(self):
        """
        Open ans store Maapi session.
        :return: Maapi session
        """
        self.session = ncs.maapi.Maapi()
        ncs.maapi.Session(self.session, self.username, self.password)

    def close_session(self):
        """
        Close self maapi session.
        :return: void
        """
        self.session.close()

    def open_transaction(self, flag='r'):
        """
        Open a Maapi transaction.
        :return: transaction, nso root
        """
        if flag == 'r':
            t = self.session.start_read_trans()
        elif flag == 'w':
            t = self.session.start_write_trans()
        else:
            raise Exception("Only possible flags are 'r' and 'w', '{}' given")
        root = ncs.maagic.get_root(t)
        return t, root

    def device_platform(self, device_name):
        """
        Get device platform.
        :param device_name: str
        :return: str
        """
        trans, root = self.open_transaction('r')
        device = root.devices.device[device_name]
        return device.platform.name

    def exec_cmd_on_device(self, device_name, command):
        """
        Issue a command on a device using nso live status
        :param device_name: device name
        :param command: command as string
        :return: command output
        """
        device = self.root().devices.device[device_name]
        execute = device.live_status.exec.any
        input = execute.get_input()
        input.args = [command]
        return execute(input).result

    def call_action(self, action_path, **kwargs):
        """
        generic method to call an action under some constraint
        :param action_path:
        :param kwargs:
        :return: action output
        """
        t, root = self.open_transaction()
        action_node = ncs.maagic.get_node(t, action_path)
        act_input = action_node.get_input()
        for k, v in kwargs.items():
            act_input[k] = v
        return action_node(act_input)

    def get_device_conf(self, device_name, show_conf_cmd=None):
        """
        Get device conf with live-status.
        :param ned: Device ned
        :param device_name: name of the device
        :return: device conf
        """
        if not show_conf_cmd:
            platform = self.device_platform(device_name)
            show_dict = {'ios': 'show running-conf',
                        'ios-xr': 'show running-conf',
                        'SR': 'admin display-conf',
                        'huawei-vrp': 'display current-conf'}
            show_conf_cmd = show_dict[platform]
        config = self.exec_cmd_on_device(device_name, show_conf_cmd)
        return config

    def check_sync(self, device_name):
        """
        Issue a check_sync command for a given device.
        :rtype: String
        :param device_name:
        """
        device = self.root()['devices']['device'][device_name]
        output = device.check_sync()
        return output['result']

    def sync_from(self, device_name):
        """
        Issue a sync-from command throughout NSO for the Device.

        :rtype: void
        :param device_name:  Device name
        """
        device = self.root().devices.device[device_name]
        output = device.sync_from()
        if not output.result:
            raise Exception(f"Device sync error for device {device_name}: {output.info}")
        self.log.info(f"synced from {device_name} : {output['result']}")

    def packages_reload(self, force=False):
        """
        Issue packages reload command on NSO and raise exception if it fails.

        :param force: force parameter of the command either true of false
        :return: output of the command
        """
        regex = "reload-result\s+\{\s+package\s(\S+)\s+result\s(true|false)(\s+info\s(.*))*\s+\}"
        self.log.info("Packages reloading ...")
        std_out = self.exec_cmd("packages reload force")
        if 'Error' in std_out:
            raise Exception(std_out.split('Error: ')[1])
        output = re.findall(regex, std_out)
        for package_name, status, _, info in output:
            if status == 'false':
                raise Exception(f"Failed to load the package '{package_name}': '{info}'")
        time.sleep(1)
        self.log.info('Package reload done with 0 error')

    def local_conf(self, device, platform):
        """
        NSO get device local conf.

        :param platform:
        :param device: device name
        :param ned: device ned
        :return: nso command output or exception
        """
        output = self.exec_cmd(f"show running-config devices device {device} config {platform}:configuration")
        return output

    def onboard_device(self, device_name, router):
        """
        Onboard a device in NSO.
        router argument must be of format
        {
        'address': x,
        'port': x,
        'auth': x,
        'type': x,
        'ned-id': x
        }

        :param device_name: device name
        :param router: device data dict
        """
        t, root = self.open_transaction("w")
        self.log.info("Setting device {} configuration ...".format(device_name))
        device_list = root.devices.device
        device = device_list.create(device_name)
        device.address = router["address"]
        device.port = router["port"]
        device.authgroup = router["auth"]
        dev_type = device.device_type[router["type"]]
        dev_type.ned_id = router["ned-id"]
        device.state.admin_state = "unlocked"
        self.log.info("Committing the device configuration...")
        t.apply()
        self.log.info("Device {} created".format(device_name))

    def connect_device(self, device_name, session=None):
        """
        NSO Connect to a device.

        :param session: maapi session
        :param device_name: device name
        """
        device = self.root().devices.device[device_name]
        self.log.info(f"Connecting device {device_name} ...")
        output = device.connect()
        self.log.info(f"Result: {output.result}")

    def fetch_host_keys(self, device_name, session=None):
        """
        NSO fetch host keys of a device.

        :type session: maapi session
        :param device_name: device name
        """
        device = self.root().devices.device[device_name]
        self.log.info("Fetching SSH keys...")
        output = device.ssh.fetch_host_keys()
        if not output.result:
            raise Exception("Fetching host key for device {} failed!".format(device_name))
        self.log.info("Result: {}".format(output.result))

    def create_auth_group(self, name, username, password):
        """
        NSO create authentication group default map.

        :param name: auth group name
        :param username: username
        :param password: password
        """
        t, root = self.open_transaction('w')
        lab = root['devices'].authgroups.group.create(name)
        map_lab1 = lab.default_map.create()
        map_lab1.remote_name = username
        map_lab1.remote_password = password
        self.log.info(f"creating authgroup '{name}' default-map ...")
        t.apply()
        self.log.info(f"authgroup '{name}' created")

    def apply_template(self, template_path, no_networking=False, encode="xml"):
        """
        apply template (load merge a payload).

        :param encode:
        :param template_path:
        :param no_networking:
        :return:
        """
        import time
        self.log.info(f"Apply template '{template_path}'")
        no_net = '-n ' if no_networking else ''
        ftype = '-F N' if encode == "json" else '-F x'
        self.run_shell_cmd(f"ncs_load -lm {no_net}{ftype} {template_path}")
        self.log.info("Template pushed successfully")

    def netsim_commit_conf(self, netsim, cmd):
        """
        Issue an Netsim command throughout shell.

        :param cmd: Command
        :return: output of the command
        """
        std_out = self.run_netsim_cmd(f"cli-c {netsim} <<EOF\nconfig\n{cmd}\ncommit\nEOF")
        return std_out

    def make_package(self, package):
        """
        Make a package.

        :param package:
        :return:
        """
        self.log.info(f"make package: '{package}'")
        self.run_shell_cmd(f"cd {self.NCS_RUN_DIR}/packages/{package}/src && make clean all")
        self.log.info(f"make package '{package}' ok")

    def get_netsim_list(self):
        """
        Get netsims list.

        :return:
        """
        import re

        std_out = self.run_netsim_cmd("list")
        devices = re.findall(r'name=(\S+)', std_out)
        return devices

    def delete_netsims(self):
        """
        Delete all netsim network.

        :return: nothing
        """
        try:
            netsims = self.get_netsim_list()
            result = self.run_netsim_cmd("delete-network")
            self.log.info(result)
            trans, root = self.open_transaction('w')
            for dev_name in netsims:
                del root.devices.device[dev_name]
            trans.apply()
        except NoNetsimDirectoryFoundError as e:
            self.log.info("no netsims to delete.")

    def start_netsim(self, device_name):
        """
        Start netsim.

        :param device_name:
        :return:
        """
        self.run_netsim_cmd(f"start {device_name}")

    def onboard_netsim(self, device=''):
        """
        Onboard netsim into nso device tree
        :param device:
        :return:
        """
        self.run_shell_cmd(f"cd {self.NETSIM_DIR} "
                           f"&& ncs-netsim ncs-xml-init {device} > device_{device}.xml "
                           f"&& ncs_load -l -m device_{device}.xml "
                           f"&& rm device_{device}.xml")

    def make_netsim(self, device_name, ned_id):
        """
        create netsim

        :param device_name:
        :param ned_id:
        :return:
        """
        try:
            self.run_netsim_cmd(f"create-device {ned_id} {device_name}")
        except Exception as e:
            self.run_netsim_cmd(f"add-device {ned_id} {device_name}")
        self.log.info(f"Netsim {device_name} created.")

    def compare_expect(self, conf1, conf2, expect_added_path, expect_removed=""):
        """
        compare two configs and expect diff
        :param conf2:
        :param expect_added_path:
        :param expect_removed:
        :return:
        """
        same, added, removed = self.compare_configs(conf1, conf2)
        if ''.join(removed.split()) != ''.join(expect_removed.split()):
            _same, _added, _removed = self.compare_configs(removed, expect_removed)
            raise Exception(f"\nremoved:::\n{_removed}\nadded:::\n{_added}\n")

        with open(expect_added_path, 'r') as f:
            expect_added = f.read()

        if ''.join(added.split()) != ''.join(expect_added.split()):
            _same, _added, _removed = self.compare_configs(added, expect_added)
            raise Exception(f"\nremoved:::\n{_removed}\nadded:::{_added}\n")

        self.log.info('>>> Compare diff conf Test ended successfully')

    def compare_configs(self, f1, f2):
        """
        Compare two configs and output diffs
        :param f1: file or str of first config
        :param f2: file or str of second config
        :return:
        """
        self.log.info("Comparing configs ...")
        added = ""
        removed = ""
        same = True
        try:
            stream1 = open(f1, "r")
            stream2 = open(f2, "r")
        except:
            from io import StringIO
            stream1 = StringIO(f1)
            stream2 = StringIO(f2)
        stream1 = stream1.readlines()
        stream2 = stream2.readlines()
        diff = difflib.ndiff(stream1, stream2)
        for line in diff:
            if line.startswith('+') and not line.startswith(('+ # Generated', '+ # Finished', '+ !!')):
                added = added + line[2:]
                same = False
            elif line.startswith('-') and not line.startswith(('- # Generated', '- # Finished', '- !!')):
                removed = removed + line[2:]
                same = False

        return [same, added, removed]

    def exec_cmd(self, cmd):
        """
        Issue an NSO command throughout shell.
        :param cmd: NSO Command
        :return: NSO output of the command
        """
        std_out = self.run_shell_cmd(f"""ncs_cli -C << EOF\n{cmd}\nEOF""")
        return std_out

    def commit_cmd(self, cmd):
        """
        commit a conf cmd on nso
        :param cmd:
        :return:
        """
        import time
        self.log.info(f"NSO commit cmd : {cmd} ...")
        try:
            self.exec_cmd(f"config\n{cmd}\ncommit")
        except SyntaxError as e:
            raise SyntaxError(f"Cmd '{cmd}' resulted in syntax error:\n {str(e)}")

    @retry
    def run_shell_cmd(self, cmd):
        """
        run shell command
        :param cmd:
        :return: std_out and std_err
        """
        self.log.debug(f"Run shell cmd: {cmd}")
        pipes = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        std_out, std_err = map(lambda x: x.decode("utf-8"), pipes.communicate())

        if pipes.returncode != 0:
            err_msg = f"{std_err}. Code: {pipes.returncode}"
            if "no registration" in std_err or "Expected create callback for state" in std_err:
                raise NoSPRegistrationError(err_msg)

            elif "out of sync" in std_err:
                raise OutOfSyncError(std_err)

            elif "Need to either specify a netsim directory" in std_err \
                    or "is not a netsim directory" in std_err:
                raise NoNetsimDirectoryFoundError(std_err)

            elif "Service still in zombie state" in std_err:
                raise StillInZombieStateError(std_err)

            else:
                raise Exception("std_err: " + err_msg)

        if len(std_err):
            self.log.debug(std_err)

        if "syntax error:" in std_out:
            raise SyntaxError(std_out.split("syntax error:")[1])

        if "Aborted:" in std_out:
            if "out of sync" in std_out:
                raise OutOfSyncError(std_out)

            elif "no registration" in std_out:
                raise NoSPRegistrationError(std_out)

            else:
                raise NsoCmdAbortedError(std_out)

        self.log.debug(f"std out: {std_out}")

        return std_out

    def run_netsim_cmd(self, cmd):
        """
        run ncs-netsim cmd
        :param cmd:
        :return: std_out of linux shell
        """
        std_out = self.run_shell_cmd(f"cd {self.NETSIM_DIR} && ncs-netsim {cmd}")
        return std_out


