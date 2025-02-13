import asyncio
import base64
import json
import sys
import websockets
import ssl
import os

from termcolor import colored

from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env


def sts_connect():
    api_key = os.getenv("DEEPGRAM_API_KEY")  # Load from environment variables

    if not api_key or api_key == "YOUR_DEEPGRAM_API_KEY":
        raise ValueError("Deepgram API Key is missing or invalid. Set DEEPGRAM_API_KEY in .env")

    headers = {
        "Authorization": f"Token {api_key}",
    }

    return websockets.connect(
        "wss://agent.deepgram.com/agent",
        extra_headers=headers,  # Send the API key as a header
    )
def load_instructions():
    with open("instructions.txt", "r", encoding="utf-8") as file:
        return file.read().strip()

async def twilio_handler(twilio_ws):
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()
    instructions = load_instructions()

    async with sts_connect() as sts_ws:
        config_message = {
            "type": "SettingsConfiguration",
            "audio": {
                "input": {
                    "encoding": "mulaw",
                    "sample_rate": 8000,
                },
                "output": {
                    "encoding": "mulaw",
                    "sample_rate": 8000,
                    "container": "none",
                },
            },
            "agent": {
                "listen": {"model": "nova-2"},
                "think": {
                    "provider": {
                        "type": "anthropic",
                    },
                    "model": "claude-3-haiku-20240307",
                    "instructions": instructions
                },
                "speak": {"model": "aura-arcas-en"},
            },
        }

        await sts_ws.send(json.dumps(config_message))

        async def sts_sender(sts_ws):
            print("sts_sender started")
            while True:
                chunk = await audio_queue.get()
                await sts_ws.send(chunk)

        async def sts_receiver(sts_ws):
            print("sts_receiver started")
            # we will wait until the twilio ws connection figures out the streamsid
            streamsid = await streamsid_queue.get()
            # for each sts result received, forward it on to the call
            async for message in sts_ws:
                if isinstance(message, str):  # Ensure message is a string
                    decoded = json.loads(message)  # Convert JSON string to dictionary

                    role = decoded.get("role", "unknown")  # Get role (user/assistant), default to 'unknown'
                    content = decoded.get("content", "").strip()  # Get message content, default to empty string

                    if role and content:
                        print(f"{role}: \"{content}\"")  # Format and print role + message

                    # handle barge-in
                    decoded = json.loads(message)
                    if decoded['type'] == 'UserStartedSpeaking':
                        clear_message = {
                            "event": "clear",
                            "streamSid": streamsid
                        }
                        await twilio_ws.send(json.dumps(clear_message))

                    continue

                raw_mulaw = message

                # construct a Twilio media message with the raw mulaw (see https://www.twilio.com/docs/voice/twiml/stream#websocket-messages---to-twilio)
                media_message = {
                    "event": "media",
                    "streamSid": streamsid,
                    "media": {"payload": base64.b64encode(raw_mulaw).decode("ascii")},
                }

                # send the TTS audio to the attached phonecall
                await twilio_ws.send(json.dumps(media_message))

        async def twilio_receiver(twilio_ws):
            print("twilio_receiver started")
            # twilio sends audio data as 160 byte messages containing 20ms of audio each
            # we will buffer 20 twilio messages corresponding to 0.4 seconds of audio to improve throughput performance
            BUFFER_SIZE = 20 * 160

            inbuffer = bytearray(b"")
            async for message in twilio_ws:
                try:
                    data = json.loads(message)
                    if data["event"] == "start":
                        print("got our streamsid")
                        start = data["start"]
                        streamsid = start["streamSid"]
                        streamsid_queue.put_nowait(streamsid)
                    if data["event"] == "connected":
                        continue
                    if data["event"] == "media":
                        media = data["media"]
                        chunk = base64.b64decode(media["payload"])
                        if media["track"] == "inbound":
                            inbuffer.extend(chunk)
                    if data["event"] == "stop":
                        break

                    # check if our buffer is ready to send to our audio_queue (and, thus, then to sts)
                    while len(inbuffer) >= BUFFER_SIZE:
                        chunk = inbuffer[:BUFFER_SIZE]
                        audio_queue.put_nowait(chunk)
                        inbuffer = inbuffer[BUFFER_SIZE:]
                except:
                    break

        # the async for loop will end if the ws connection from twilio dies
        # and if this happens, we should forward an some kind of message to sts
        # to signal sts to send back remaining messages before closing(?)
        # audio_queue.put_nowait(b'')

        await asyncio.wait(
            [
                asyncio.ensure_future(sts_sender(sts_ws)),
                asyncio.ensure_future(sts_receiver(sts_ws)),
                asyncio.ensure_future(twilio_receiver(twilio_ws)),
            ]
        )

        await twilio_ws.close()


async def router(websocket, path):
    print(f"Incoming connection on path: {path}")
    if path == "/twilio":
        print("Starting Twilio handler")
        await twilio_handler(websocket)

def main():
    # use this if using ssl
    # ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # ssl_context.load_cert_chain('cert.pem', 'key.pem')
    # server = websockets.serve(router, '0.0.0.0', 443, ssl=ssl_context)

    # use this if not using ssl
    server = websockets.serve(router, "0.0.0.0", 5000)
    print("Server starting on ws://localhost:5000")

    asyncio.get_event_loop().run_until_complete(server)
    asyncio.get_event_loop().run_forever()


if __name__ == "__main__":
    sys.exit(main() or 0)
