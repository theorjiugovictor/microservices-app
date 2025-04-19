import os, gridfs, pika, json, logging
from flask import Flask, request, send_file, jsonify
from flask_pymongo import PyMongo
from auth import validate
from auth_svc import access
from storage import util
from bson.objectid import ObjectId
from werkzeug.middleware.dispatcher import DispatcherMiddleware

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

server = Flask(__name__)

try:
    # MongoDB connections
    mongo_video = PyMongo(server, uri=os.environ.get('MONGODB_VIDEOS_URI'))
    mongo_mp3 = PyMongo(server, uri=os.environ.get('MONGODB_MP3S_URI'))
    logger.info("MongoDB connections established successfully")

    fs_videos = gridfs.GridFS(mongo_video.db)
    fs_mp3s = gridfs.GridFS(mongo_mp3.db)

    # RabbitMQ connection
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host="rabbitmq", 
        heartbeat=0,
        connection_attempts=5,
        retry_delay=5
    ))
    channel = connection.channel()
    logger.info("RabbitMQ connection established successfully")

except Exception as e:
    logger.error(f"Startup error: {str(e)}", exc_info=True)
    raise

@server.route("/login", methods=["POST"])
def login():
    try:
        logger.debug("Login attempt received")
        token, err = access.login(request)

        if not err:
            logger.info("Login successful")
            return token
        else:
            logger.error(f"Login error: {err}")
            return err
    except Exception as e:
        logger.error(f"Login exception: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@server.route("/upload", methods=["POST"])
def upload():
    try:
        logger.debug("Upload request received")
        logger.debug(f"Request headers: {dict(request.headers)}")
        
        access, err = validate.token(request)
        if err:
            logger.error(f"Token validation error: {err}")
            return err

        access = json.loads(access)
        logger.debug(f"Access info: {access}")

        if access["admin"]:
            logger.debug(f"Files in request: {request.files}")
            if len(request.files) > 1 or len(request.files) < 1:
                logger.error("Invalid number of files")
                return "exactly 1 file required", 400

            for filename, f in request.files.items():
                logger.debug(f"Processing file: {filename}")
                err = util.upload(f, fs_videos, channel, access)

                if err:
                    logger.error(f"Upload error: {err}")
                    return err

            logger.info("Upload successful")
            return "success!", 200
        else:
            logger.warning("Unauthorized access attempt")
            return "not authorized", 401
    except Exception as e:
        logger.error(f"Upload exception: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@server.route("/download", methods=["GET"])
def download():
    try:
        logger.debug("Download request received")
        access, err = validate.token(request)

        if err:
            logger.error(f"Token validation error: {err}")
            return err

        access = json.loads(access)
        logger.debug(f"Access info: {access}")

        if access["admin"]:
            fid_string = request.args.get("fid")

            if not fid_string:
                logger.error("Missing fid parameter")
                return "fid is required", 400

            try:
                out = fs_mp3s.get(ObjectId(fid_string))
                logger.info(f"File {fid_string} downloaded successfully")
                return send_file(out, download_name=f"{fid_string}.mp3")
            except Exception as err:
                logger.error(f"Download error: {str(err)}", exc_info=True)
                return "internal server error", 500

        logger.warning("Unauthorized access attempt")
        return "not authorized", 401
    except Exception as e:
        logger.error(f"Download exception: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@server.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for debugging"""
    try:
        status = {
            "mongodb_video": mongo_video.db.command("ping") == {"ok": 1},
            "mongodb_mp3": mongo_mp3.db.command("ping") == {"ok": 1},
            "rabbitmq": connection.is_open
        }
        return jsonify(status), 200
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    server.run(host="0.0.0.0", port=8080)
