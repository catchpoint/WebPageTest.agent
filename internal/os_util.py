# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Cross-platform support for os-level things that differ on different platforms"""
import logging
import platform
import subprocess

def kill_all(exe, force):
    """Terminate all instances of the given process"""
    logging.debug("Terminating all instances of %s", exe)
    plat = platform.system()
    if plat == "Windows":
        if force:
            subprocess.call(['taskkill', '/F', '/T', '/IM', exe])
        else:
            subprocess.call(['taskkill', '/IM', exe])
    elif plat == "Linux" or plat == "Darwin":
        if force:
            subprocess.call(['killall', '-s', 'SIGKILL', exe])
        else:
            subprocess.call(['killall', exe])


def launch_process(command_line):
    """Start a process using platform-specific support"""
    logging.debug(command_line)
    if platform.system() == 'Windows':
        import win32api
        import win32con
        import win32process
        startupinfo = win32process.STARTUPINFO()
        startupinfo.wShowWindow = 1 #SW_SHOWNORMAL
        process_handle, thread_handle, proc, _ = \
            win32process.CreateProcess(None,             # executable
                                       command_line,     # command-line
                                       None,             # process attributes
                                       None,             # security attributes
                                       0,                # inherit handles
                                       win32con.NORMAL_PRIORITY_CLASS,
                                       None,             # new environment
                                       None,             # current directory
                                       startupinfo)      #startupinfo
        if process_handle:
            win32api.CloseHandle(process_handle)
        if thread_handle:
            win32api.CloseHandle(thread_handle)
    else:
        proc = subprocess.Popen(command_line, shell=True)
    return proc


def stop_process(proc):
    """Stop a process using platform-specific support"""
    if platform.system() != 'Windows':
        proc.terminate()
        proc.kill()


def flush_dns():
    """Flush the OS DNS resolver"""
    logging.debug("Flushing DNS")
    plat = platform.system()
    if plat == "Windows":
        subprocess.call(['ipconfig', '/flushdns'])
    elif plat == "Darwin":
        subprocess.call(['sudo', 'killall', '-HUP', 'mDNSResponder'])
        subprocess.call(['sudo', 'dscacheutil', '-flushcache'])
        subprocess.call(['sudo', 'lookupd', '-flushcache'])
    elif plat == "Linux":
        subprocess.call(['sudo', 'service', 'dnsmasq', 'restart'])
        subprocess.call(['sudo', 'rndc', 'restart'])

def run_elevated(command, args):
    """Run the given command as an elevated user and wait for it to return"""
    ret = 1
    if command.find(' ') > -1:
        command = '"' + command + '"'
    if platform.system() == 'Windows':
        import win32api
        import win32con
        import win32event
        import win32process
        from win32com.shell.shell import ShellExecuteEx
        from win32com.shell import shellcon
        logging.debug(command + ' ' + args)
        process_info = ShellExecuteEx(nShow=win32con.SW_SHOWNORMAL,
                                      fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
                                      lpVerb='runas',
                                      lpFile=command,
                                      lpParameters=args)
        win32event.WaitForSingleObject(process_info['hProcess'], win32event.INFINITE)
        ret = win32process.GetExitCodeProcess(process_info['hProcess'])
        win32api.CloseHandle(process_info['hProcess'])
    else:
        logging.debug('sudo ' + command + ' ' + args)
        ret = subprocess.call('sudo ' + command + ' ' + args, shell=True)
    return ret
