import datetime as dt
import threading
import time
from shutil import copyfile
from os.path import isfile
import json

import yaml
import logging
import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
import requests as reqs
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s] (%(threadName)-10s) %(message)s')


class DotDict(dict):
    def __getattr__(*args):
        val = dict.get(*args)
        return DotDict(val) if type(val) is dict else val

    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


def log(message, end=False, err=False, warn=False, exit_code=1):
    if type(message) != str:
        message = str(message)
    if err:
        logging.error(message)
    elif warn:
        logging.warning(message)
    else:
        logging.debug(message)
    if end:
        exit(exit_code)


def load_config(raw=False):
    with open(r'./config.yaml') as cf:
        try:
            conf = yaml.load(cf, Loader=yaml.FullLoader)
            # conf = DotDict(conf)
            copyfile(cf.name, './config-backup.yaml')
        except yaml.YAMLError:
            log("[BAD CONFIG] YAML Configuration Error. Using backup config...", warn=True)
            if not isfile('./config-backup.yaml'):
                log("[CONFIG ERROR] No backup config found. Exiting application!!!", end=True, err=True)
            with open(r'./config-backup.yaml') as bu:
                conf = yaml.load(bu, Loader=yaml.FullLoader)

    # log(conf)
    return conf if raw else DotDict(conf)


last_triggered = time.time()


def on_config_modified(event):
    global last_triggered
    if event.src_path.find('~') == -1 and (time.time() - last_triggered) > 1:
        config = load_config()
        log("Config modified callback ")
        publish.single("{sys}/{evt}".format(sys=config.meta.system_evt_key, evt=config.meta.system_init_evt),
                       hostname=config.broker.host)
        log("Published Re-init event. Config updated! " + "{sys}/{evt}".format(sys=config.meta.system_evt_key,
                                                                               evt=config.meta.system_init_evt))
        last_triggered = time.time()


def config_modified_event():
    patterns = ["*.yaml"]
    ignore_patterns = ["*-backup.yaml"]
    ignore_directories = True
    case_sensitive = True

    listener = PatternMatchingEventHandler(patterns, ignore_patterns, ignore_directories, case_sensitive)
    listener.on_modified = on_config_modified

    path = "."
    go_recursively = False
    observer = Observer()
    observer.schedule(listener, path, recursive=go_recursively)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def on_connect(client, userdata, flags, rc):
    config = load_config()
    log("Connected to broker client " + str(client))
    # Sensor Network event subscription
    for name in config[config.meta.sensors_key]:
        sensor = config[config.meta.sensors_key][name]
        for sub in sensor[config.meta.topic_key][config.meta.sub_key]:
            topic = sensor[config.meta.topic_key][config.meta.sub_key][sub]
            if len(topic):
                client.subscribe("{id}/{prop}".format(id=sensor[config.meta.sensor_id_key], prop=topic))

    # System check event subscription
    for evt in config[config.meta.system_evt_key][config.meta.sub_key]:
        if len(evt):
            client.subscribe("{syst}/{evt}".format(syst=config.meta.system_evt_key, evt=evt))
    # Register latest configuration
    # Check config file status
    with open('./config.yaml', 'rb') as cf:
        try:
            yaml.load(cf, Loader=yaml.FullLoader)
            correct = True
        except yaml.YAMLError:
            if isfile('./config-backup.yaml'):
                cf = open('./config-backup.yaml', 'rb')
                correct = True
        finally:
            if correct:
                http = reqs.post("{host}{uri}".format(host=config.remote.host, uri=config.remote.submit_config),
                                 files={'file': cf})
                log(http.json())
            cf.close()


def on_message(client, userdata, msg):
    config = load_config()
    log("New message: " + str(msg.topic) + " " + str(msg.payload))
    if msg.topic == "{sys}/{evt}".format(sys=config.meta.system_evt_key, evt=config.meta.system_init_evt):
        client.reconnect()
        # client.reinitialise(host=config.broker.host)
        log("Reconnected to broker...")
        return
    sensors = config[config.meta.sensors_key]
    key, topic = str(msg.topic).split("/")
    if key in sensors:
        payload = {'key': key, 'topic': topic, 'endpoint': str(msg.topic), 'data': msg.payload}
        log("Message: {}".format(payload))
        http = reqs.post("{host}{uri}/".format(host=config.remote.host, uri=config.remote.value_endpoint), data=payload)
        if http.status_code != 200:
            log("HTTP Error [{code}] {msg}".format(code=http.status_code, msg=http.text))
    else:
        log("[Sensor Error] Sensor NOT FOUND `{sensor}`".format(sensor=str(msg.topic)))


def on_log():
    pass


def main():
    config = load_config()
    client = mqtt.Client()
    client.on_message = on_message
    client.on_connect = on_connect
    # client.enable_logger(logger=logging)  # Set logger

    # Disconnection call
    # Triggered when client suddenly disconnects without call .disconnect()
    client.will_set(topic="{sys}/{evt}".format(sys=config.meta.system_evt_key, evt=config.meta.system_offline_evt),
                    payload=str(dt.datetime.now()))
    # Actual connection
    client.connect(config.broker.host, config.broker.port, config.broker.keepalive)

    # Start config.yaml watcher thread (without which watchdog will run a blocking thread)
    thread = threading.Thread(name='config_watcher', target=config_modified_event)
    thread.setDaemon(True)
    thread.start()
    thread.join(1)

    # Network looping
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        log("Safe Exit [Keyboard Interruption]")


if __name__ == '__main__':
    main()
