from dotenv import load_dotenv
import os

load_dotenv()

print("Broker:", os.getenv("MQTT_BROKER"))
print("Port:", os.getenv("MQTT_PORT"))
print("Username:", os.getenv("MQTT_USERNAME"))
print("Topic:", os.getenv("MQTT_TOPIC"))
print("Password loaded:", bool(os.getenv("MQTT_PASSWORD")))
