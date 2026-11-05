#!/usr/bin/env python3
from flask import Flask, request, Response, send_file
import jsonpickle
import base64
import hashlib
import io
import os
import sys
import platform
import redis
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

redisClient = redis.StrictRedis(host=redisHost, port=redisPort, db=0)
minioClient = Minio(minioHost, secure=False,
                    access_key=minioUser, secret_key=minioPasswd)

infoKey = "{}.rest.info".format(platform.node())
debugKey = "{}.rest.debug".format(platform.node())

def log_debug(message, key=debugKey):
    print("DEBUG:", message, file=sys.stdout)
    redisClient.lpush('logging', f"{debugKey}:{message}")

def log_info(message, key=infoKey):
    print("INFO:", message, file=sys.stdout)
    redisClient.lpush('logging', f"{infoKey}:{message}")

def make_buckets():
    for bucket in [queueBucket, outputBucket]:
        if not minioClient.bucket_exists(bucket):
            log_info(f"Creating bucket {bucket}")
            minioClient.make_bucket(bucket)

def json_response(body, status=200):
    return Response(response=jsonpickle.encode(body), status=status,
                    mimetype="application/json")

def track_object_name(songhash, track):
    if not track.endswith(".mp3"):
        track = track + ".mp3"
    return f"{songhash}-{track}"

app = Flask(__name__)

# needed for the GKE load balancer health check
@app.route('/', methods=['GET'])
def hello():
    return '<h1> Music Separation Server</h1><p> Use a valid endpoint </p>'

@app.route('/apiv1/separate', methods=['POST'])
def separate():
    try:
        data = jsonpickle.decode(request.data)
        mp3 = base64.b64decode(data['mp3'])
        songhash = hashlib.sha224(mp3).hexdigest()

        make_buckets()
        minioClient.put_object(queueBucket, f"{songhash}.mp3",
                               io.BytesIO(mp3), len(mp3),
                               content_type="audio/mpeg")

        work = {
            "hash": songhash,
            "model": data.get("model"),
            "callback": data.get("callback"),
        }
        redisClient.lpush(workQueue, jsonpickle.encode(work))
        log_info(f"Queued {songhash} for separation ({len(mp3)} bytes)")

        return json_response({"hash": songhash,
                              "reason": "Song enqueued for separation"})
    except Exception as exp:
        log_info(f"separate failed: {exp}")
        return json_response({"error": str(exp)}, status=500)

@app.route('/apiv1/queue', methods=['GET'])
@app.route('/apiv1/queue/', methods=['GET'])
def queue():
    entries = []
    for item in redisClient.lrange(workQueue, 0, -1):
        try:
            entries.append(jsonpickle.decode(item)["hash"])
        except Exception:
            entries.append(item.decode('utf-8'))
    return json_response({"queue": entries})

@app.route('/apiv1/track/<songhash>/<track>', methods=['GET'])
def get_track(songhash, track):
    name = track_object_name(songhash, track)
    try:
        resp = minioClient.get_object(outputBucket, name)
        mp3 = resp.read()
        resp.close()
        resp.release_conn()
    except Exception as exp:
        log_debug(f"track {name} not found: {exp}")
        return json_response({"error": f"{name} not found"}, status=404)
    log_debug(f"Sending {name} ({len(mp3)} bytes)")
    return send_file(io.BytesIO(mp3), mimetype="audio/mpeg",
                     as_attachment=True, download_name=name)

@app.route('/apiv1/remove/<songhash>/<track>', methods=['GET', 'DELETE'])
def remove_track(songhash, track):
    name = track_object_name(songhash, track)
    try:
        minioClient.remove_object(outputBucket, name)
    except Exception as exp:
        return json_response({"error": str(exp)}, status=500)
    log_info(f"Removed {name}")
    return json_response({"hash": songhash, "track": track,
                          "reason": "Track removed"})

if __name__ == "__main__":
    log_info("Starting rest server")
    app.run(host="0.0.0.0", port=5000)
