#!/usr/bin/python
# -*- coding: utf-8 -*-

import MySQLdb
import os
import time
import sys
import pycurl
import threading
from warnings import filterwarnings

filterwarnings('ignore', category = MySQLdb.Warning)

test_dbs = [
    {
        "slaves": [
            {
            "host":"115.28.6.205",
            "port":3312,
            "user":"test",
            "passwd":"2503",
            },
            {
            "host":"115.28.6.205",
            "port":3313,
            "user":"test",
            "passwd":"2503",
            },
        ],
        "master": {
            "host":"115.28.6.205",
            "port":3311,
            "user":"test",
            "passwd":"2503",
        }
    },
    # add more
]

tglobal = threading.local()

def alert(msg, type):
    print msg, type
    log(msg)


class Single(threading.Thread):

    def __init__(self, routine, suit = {}, globalvars = {}):
        threading.Thread.__init__(self)
        self._routine = routine
        self._suit = suit

    def run(self):
        tglobal.dbconns = []
        tglobal.suit = self._suit
        self._routine()

def do(func, params, repeat_times = 0, doalert=True):
    time.sleep(0.5)
    repeat_times += 1
    error = ""
    ret = ""
    while repeat_times:
        repeat_times -= 1
        try:
            ret = func(**params)
        except Exception, e:
            error = e.args[0]
            continue
        break

    if error != "": 
        if repeat_times != 0:
            type = "mail"
        else:
            type = "sms"
        if doalert: 
            alert(error, type)
        return False
    return ret

def check_process():
    pass

def slave_change_master(slavedb, master_host, master_port, 
                master_logfile, master_logpos, master_user, master_password):
    cursor = slavedb.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("stop slave;")
    cursor.execute("change master to master_host='{host}', master_user='{user}',\
master_password='{password}', master_port={port}, master_log_file='{logfile}', \
master_log_pos={logpos};".format(host=master_host, user=master_user, \
    password=master_password, port=master_port,logfile=master_logfile,\
    logpos=master_logpos))
    cursor.execute("start slave;")
    return True

def slave_become_master(slavedb):
    cursor = slavedb.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("stop slave;")
    cursor.execute("reset master;")
    cursor.execute("show master status;")
    ret = cursor.fetchall()[0]
    master_logfile = ret["File"]
    master_logpos = ret["Position"]
    return (master_logfile, master_logpos)

def iosql_thread(slavedb, host='', port=''):
    cursor = slavedb.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("show slave status")
    ret = cursor.fetchall()[0]
    sqlt_status = ret["Slave_SQL_Running"]
    iot_status = ret["Slave_IO_Running"]
    if sqlt_status != "Yes":
        raise Exception("{host} {port} slave sql thread is not yes"\
              .format(host=host, port=port))
    if iot_status != "Yes":
        raise Exception("{host} {port} slave io thread is not yes"\
              .format(host=host, port=port))
    return True

def repl_delay(slavedb, host='', port=''):
    cursor = slavedb.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("show slave status")
    ret = cursor.fetchall()[0]
    read_master_log_pos = ret["Read_Master_Log_Pos"]
    exec_master_log_pos = ret["Exec_Master_Log_Pos"]
    if read_master_log_pos != exec_master_log_pos:
        raise Exception("{host} {port} slave delay".format(host=host, port=port))
    return exec_master_log_pos

def same_logfile(slavedb):
    cursor = slavedb.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("show slave status")
    ret = cursor.fetchall()[0]
    master_log_file = ret["Master_Log_File"]
    relay_master_log_file = ret["Relay_Master_Log_File"]
    if master_log_file != relay_master_log_file:
        raise Exception("replay_master_log_file is not the same as master_log_file")
    return master_log_file

def log(msg):
    nowtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))
    fh = open("./logfile", "a")
    fh.write("[%s] %s\n" % (nowtime, msg))

def change_master(slaves):
    setmaster = False
    curpos = 0

    for slave in slaves:
        # 1. connect to any slaves that belongs to the master
        slavedb = do(connect, slave, 3, False)
        if slavedb == False:
            continue
        # 2. check if it is the same logfile
        logfile = do(same_logfile, {"slavedb": slavedb}, 3, False)
        if logfile == False:
            continue
        # 3. check if it is relay, give it 5 chances to be synced
        logpos  = do(repl_delay, {"slavedb": slavedb}, 5, False)
        if logpos == False:
            continue

        if not setmaster or curpos < logpos:
            setmaster = slave
            curpos = logpos
    if not setmaster:
        # raise Exception("None of the slaves can be raised as master") # disaster, no need to alert any more
        return False
    bemasterdb = do(connect, setmaster, 3)
    master_logfile, master_logpos = do(slave_become_master, {"slavedb": bemasterdb})
    master_host, master_port = setmaster["host"], setmaster["port"]
    slaves.remove(setmaster)
    for slave in slaves:
        changeinfo = {
            "slavedb"        : do(connect, slave, 3),
            "master_host"    : master_host,
            "master_password": "repl",
            "master_user"    : "repl",
            "master_port"    : master_port,
            "master_logpos"  : master_logpos,
            "master_logfile" : master_logfile,
        }
        do(slave_change_master, changeinfo, 0, False)

def connect(host, user, passwd, port):
    try:
        db = MySQLdb.connect(host=host, user=user, passwd=passwd, port=port, connect_timeout=1)
    except Exception, e:
        raise Exception("{host} {port} {sql_err}"\
        .format(host=host, port=port, sql_err=e.args[1]))
    tglobal.dbconns.append(db)
    return db

def cleardb():
    while tglobal.dbconns:
        tglobal.dbconns.pop().close()

def slave_routine(slave):
    slavedb = do(connect, slave, 5)
    if slavedb == False:
        return
    ret = do(iosql_thread, {"slavedb": slavedb, "host": slave["host"], "port": slave["port"]})
    if ret == False:
        return
    do(repl_delay, {"slavedb": slavedb, "host": slave["host"], "port": slave["port"]})

def master_routine(master):
    db = do(connect, master, 5)
    if not db:
        do(change_master, {"slaves": tglobal.suit["slaves"]})

def suit_routine():
    master_routine(tglobal.suit["master"])
    for slave in tglobal.suit["slaves"]:
        slave_routine(slave)

if __name__ == "__main__":
    for suit in test_dbs:
        thread = Single(suit_routine, suit)
        thread.start()
