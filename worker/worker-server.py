#!/usr/bin/env python3
import glob
import os
import shutil
import sys
import platform
import jsonpickle
import redis
import requests
from minio import Minio

##
## Configure test vs. production
##
redisHost = os.getenv("REDIS_HOST") or "localhost"
redisPort = os.getenv("REDIS_PORT") or 6379

minioHost = os.getenv("MINIO_HOST") or "localhost:9000"
minioUser = os.getenv("MINIO_USER") or "rootuser"
minioPasswd = os.getenv("MINIO_PASSWD") or "rootpass123"

queueBucket = "queue"
outputBucket = "output"
workQueue = "toWorker"
parts = ["bass", "drums", "vocals", "other"]

redisClient = redis.StrictRedis(host=redisHost, port=redisPort, db=0)
minioClient = Minio(minioHost, secure=False,
                    access_key=minioUser, secret_key=minioPasswd)

infoKey = "{}.worker.info".format(platform.node())
debugKey = "{}.worker.debug".format(platform.node())

def log_debug(message, key=debugKey):
    print("DEBUG:", message, file=sys.stdout)
    redisClient.lpush('logging', f"{debugKey}:{message}")

def log_info(message, key=infoKey):
    print("INFO:", message, file=sys.stdout)
    redisClient.lpush('logging', f"{infoKey}:{message}")

def separate(work):
    songhash = work["hash"]
    inputFile = f"/tmp/{songhash}.mp3"
    outputDir = "/tmp/output"

    log_info(f"Downloading {songhash}.mp3 from bucket {queueBucket}")
    minioClient.fget_object(queueBucket, f"{songhash}.mp3", inputFile)

    model = work.get("model") or os.getenv("DEMUCS_MODEL")
    modelArg = f"-n {model}" if model else ""
    cmd = f"python3 -m demucs.separate --mp3 {modelArg} --out {outputDir} {inputFile}"
    log_info(f"Running: {cmd}")
    status = os.system(cmd)
    if status != 0:
        raise RuntimeError(f"demucs failed with status {status}")

    if not minioClient.bucket_exists(outputBucket):
        minioClient.make_bucket(outputBucket)

    for part in parts:
        found = glob.glob(f"{outputDir}/*/{songhash}/{part}.mp3")
        if not found:
            log_info(f"Missing output for {part}")
            continue
        objName = f"{songhash}-{part}.mp3"
        minioClient.fput_object(outputBucket, objName, found[0],
                                content_type="audio/mpeg")
        log_info(f"Uploaded {objName}")

    os.remove(inputFile)
    for d in glob.glob(f"{outputDir}/*/{songhash}"):
        shutil.rmtree(d, ignore_errors=True)
    minioClient.remove_object(queueBucket, f"{songhash}.mp3")

def do_callback(work):
    callback = work.get("callback")
    if not callback or "url" not in callback:
        return
    try:
        requests.post(callback["url"], json=callback.get("data"), timeout=5)
        log_debug(f"Callback to {callback['url']} sent")
    except Exception as exp:
        log_debug(f"Callback to {callback['url']} failed: {exp}")

log_info("Worker started, waiting for work")
while True:
    try:
        item = redisClient.blpop(workQueue, timeout=0)
        work = jsonpickle.decode(item[1])
        log_info(f"Got work for {work['hash']}")
        separate(work)
        do_callback(work)
        log_info(f"Finished {work['hash']}")
    except Exception as exp:
        log_info(f"Exception in worker loop: {exp}")
    sys.stdout.flush()
    sys.stderr.flush()
