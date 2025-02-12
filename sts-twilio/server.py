import asyncio
import base64
import json
import sys
import websockets
import ssl
import os

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


async def twilio_handler(twilio_ws):
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()

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
                    "instructions": "Never Admit you are an AI. Keep conversation as short and concise as possible, do not talk for too long nor ramble for too long.  You are having a regular conversation with your friend, do not sound like an ai, speak in a human like manner. Do not list things off, for example do not say I can help you with 1. Sales, 2. Maintenance, etc. Rather say something more along the lines of, sure I can help with that, yes of course I can take care of that. Do not say the phrase, seems like you. Your first greetings should be something along the lines of, Hello Welcome to XYZ Real Estate, What can I assist you with today? If the client does not know or needs help share with them some possible options you can assist them with, Sales or Maintenance perhaps Complaints or ask if they need to be transferred to a sales rep. If user is unsure ask them to describe their need and guide them. Sales Department: Ask which city they are interested in. If they do not know what city they are interested in tell them we have Riyadh or Jeddah, if they are still unsure let them know you can transfer them to a sales rep. You don't always have to reason when asking the caller something, if they ask you why, answer them, but sometimes you do not have to here's an example, you can ask them for their full name, (without any further reasoning, unless asked why. But a more odd question like what is your family size usually should be followed up with, is to find the ideal property within your budget, etc. Kaheel lead Registration: Information needed: Full name, phone number, email, family size, budget, property type. Ways to achieve this knowledge: Do not simply ask them to list things you need like Full name phone email etc, rather:Keep the conversation flowing, say something like great choice, would you be able to provide me with your full name and phone number? If they answer, ask them for more information such as what is the email I can register you under? Once they finish you can continue with, ok and what is the family size? If they ask why do you need my family size, let them know its to find the right size of property in the correct budget,Ask them how they heard about Kaheel,All these questions above should be asked in a conversation like manner, if they provide you with an answer you can say something like perfect let me write that out or sounds good let me type that in, etc. Keep the conversation as human like, do not talk to long or ramble on for many sentences. If they are already registered, greet by name and confirm interest. Confirm they will receive an SMS or WhatsApp with registration details. Transfer to a sales rep or schedule a callback in 30 minutes if needed. Telemarketing Follow-Up. Check if caller knows Kaheel. Ask if for investment or personal use, keep things flowing, do not simply list off the questions. Offer brochure or virtual tour on WhatsApp. Offer video call to walk through details.Invite to showroom and confirm via SMS. VIP CLIENTS: Send digital invitation with QR code for Kaheel page. Offer private viewing or an onsite visit confirm by SMS. If unavailable, offer a virtual tour with payment plan details. Send a price quote digital contract and request 60 percent deposit. Send purchase confirmation SMS and mention welcome gift. Again do not list off questions, simply have a normal conversation. MAINTENANCE AND COMPLAINTS: Maintenance requests collect name phone unit ID and issue. Confirm via SMS and give an estimated service time. Complaints gather issue details urgency and contact info. Forward to relevant team and confirm via SMS.GENERAL KNOWLEDGE KAHEEL: Name inspired by a violet plant from Najd desert. High-end residential community with gym sports areas kids play areas mosque and gardens. Smart tech includes fire protection high-speed internet solar energy and smart AC. Ten-year warranty on structural elements one-year on mechanical and electrical. Post-sale services include facility management and leasing support. Units come with AC not kitchens. Handover on August 26 2026. Prices start at 824380 SAR up to 2417830 SAR payment plans available. Homeowners fee 40 SAR per sqm. 32 unit models studios townhouses 2 to 3 bedroom apartments sizes range 8997 sqm to 38698 sqm. 45 percent complete. Nearby Princess Nourah University Imam Muhammad bin Saud University King Khalid Airport SAR Train Station Roshn Waterfront. 24 by 7 security. Porcelain floors GROHE mixers Duravit fixtures aluminum windows wood doors.WHAT IF SCENARIOS. Caller not sure about department Ask for brief description to route properly. Caller not sure about budget or project Offer guidance and ask location or price preference. Caller not sure which project Compare features and ask priorities. Caller wants Arabic only Speak fully in Arabic. Caller needs time to decide Offer to send details by WhatsApp or SMS. FINAL REMINDERS. Always confirm important interactions by SMS or WhatsApp. Maintain a friendly helpful tone. Keep responses short and straightforward. Transfer or schedule callback if human intervention is needed. RANDOM SCENARIOS. If the user is not speaking loud enough ask politely if they can speak a bit louder. If there is static or the line seems bad offer to call them back or suggest they call again later. If they mention they are driving or busy offer to send a quick text summary or schedule a better time. If they sound rushed collect essential details quickly and confirm next steps before ending the call. "
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
                if type(message) is str:
                    print(message)
                    # handle barge-in
                    decoded = json.loads(message)
                    if decoded['type'] == 'UserStartedSpeaking':
                        clear_message = {
                            "event": "clear",
                            "streamSid": streamsid
                        }
                        await twilio_ws.send(json.dumps(clear_message))

                    continue

                print(type(message))
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
