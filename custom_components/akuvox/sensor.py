"""Sensor platform for akuvox."""
from datetime import datetime
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util
from homeassistant.helpers import storage
from homeassistant.helpers.entity import DeviceInfo

from .api import AkuvoxApiClient
from .coordinator import AkuvoxDataUpdateCoordinator
from .const import (
    DOMAIN,
    LOGGER,
    NAME,
    VERSION,
    DATA_STORAGE_KEY,
    DOOR_LOG_STATUS_OPTIONS,
    SIGNAL_DOOR_LOG_UPDATE,
)
from .entity import AkuvoxEntity

# Akuvox has returned temporary key dates in several formats depending on
# region and server version, e.g. "18-06-2026 10:29:57",
# "2026-06-18 10:29:57" and "06-18-2026 10:29:57 AM".
AKUVOX_DATE_FORMATS = (
    "%d-%m-%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%m-%d-%Y %I:%M:%S %p",
    "%d-%m-%Y %I:%M:%S %p",
    "%Y-%m-%d %I:%M:%S %p",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %I:%M:%S %p",
)


def parse_akuvox_datetime(value) -> datetime | None:
    """Parse a date string from the Akuvox API, trying all known formats."""
    text = str(value).strip()
    for date_format in AKUVOX_DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            continue
    return None

async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the temporary door key platform."""
    coordinator: AkuvoxDataUpdateCoordinator
    for _key, value in hass.data[DOMAIN].items():
        coordinator = value
    client = coordinator.client
    store = storage.Store(hass, 1, DATA_STORAGE_KEY)
    device_data: dict = await store.async_load() # type: ignore
    door_keys_data = (device_data or {}).get("door_keys_data") or []

    entities = [
        AkuvoxDoorLogStatusSensor(client=client, entry=entry),
        AkuvoxLastDoorEventSensor(client=client, entry=entry),
    ]
    for door_key_data in door_keys_data:
        key_id = door_key_data["key_id"]
        description = door_key_data["description"]
        key_code=door_key_data["key_code"]
        begin_time = parse_akuvox_datetime(door_key_data["begin_time"])
        end_time = parse_akuvox_datetime(door_key_data["end_time"])
        if begin_time is None or end_time is None:
            LOGGER.warning("Skipping temporary key '%s': unrecognised date format (%s / %s)",
                           key_id, door_key_data["begin_time"], door_key_data["end_time"])
            continue
        allowed_times=door_key_data["allowed_times"]
        access_times=door_key_data["access_times"]
        qr_code_url=door_key_data["qr_code_url"]

        entities.append(
            AkuvoxTemporaryDoorKey(
                client=client,
                entry=entry,
                key_id=key_id,
                description=description,
                key_code=key_code,
                begin_time=begin_time,
                end_time=end_time,
                allowed_times=allowed_times,
                access_times=access_times,
                qr_code_url=qr_code_url,
            )
        )

    async_add_devices(entities)

class AkuvoxTemporaryDoorKey(SensorEntity, AkuvoxEntity):
    """Akuvox temporary door key class."""

    def __init__(
        self,
        client: AkuvoxApiClient,
        entry,
        key_id: str,
        description: str,
        key_code: str,
        begin_time: datetime,
        end_time: datetime,
        allowed_times: int,
        access_times: int,
        qr_code_url) -> None:
        """Initialize the Akuvox door relay class."""
        super(SensorEntity, self).__init__(client=client, entry=entry)
        AkuvoxEntity.__init__(
            self=self,
            client=client,
            entry=entry
        )
        self.client = client
        self.key_id = key_id
        self.description = description
        self.key_code = key_code
        self.begin_time = begin_time
        self.end_time = end_time
        self.allowed_times = allowed_times
        self.access_times = access_times
        self.qr_code_url = qr_code_url
        self.expired = False

        name = f"{self.description} {self.key_id}".strip()
        self._attr_unique_id = name
        self._attr_name = name
        self._attr_key_code = key_code

        self._attr_extra_state_attributes = self.to_dict()

        LOGGER.debug("Adding temporary door key '%s'", self._attr_unique_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "Temporary Keys")},  # type: ignore
            name="Temporary Keys",
            model=VERSION,
            manufacturer=NAME,
        )

    def is_key_active(self):
        """Check if the key is currently active based on the begin_time and end_time."""
        current_time = datetime.now()
        return self.begin_time <= current_time <= self.end_time

    def to_dict(self):
        """Convert the object to a dictionary for easy serialization."""
        return {
            'key_id': self.key_id,
            'description': self.description,
            'key_code': self.key_code,
            'enabled': self.is_key_active(),
            'begin_time': self.begin_time,
            'end_time': self.end_time,
            'access_times': self.access_times,
            'allowed_times': self.allowed_times,
            'qr_code_url': self.qr_code_url,
            'expired': not self.is_key_active()
        }


def _door_log_device_info(entry) -> DeviceInfo:
    """Device that groups the door log sensors."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_door_log")},  # type: ignore
        name="Akuvox door log",
        model=VERSION,
        manufacturer=NAME,
    )


class AkuvoxDoorLogStatusSensor(SensorEntity):
    """Shows whether door bell / door open events can be received."""

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = DOOR_LOG_STATUS_OPTIONS
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:doorbell"

    def __init__(self, client: AkuvoxApiClient, entry) -> None:
        """Initialize the door log status sensor."""
        self.client = client
        self._attr_name = "Akuvox door log status"
        self._attr_unique_id = f"{entry.entry_id}_door_log_status"
        self._attr_device_info = _door_log_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Listen for door log updates."""
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_DOOR_LOG_UPDATE, self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self):
        """Current door log status."""
        status = getattr(self.client, "door_log_status", "starting")
        return status if status in DOOR_LOG_STATUS_OPTIONS else "error"

    @property
    def extra_state_attributes(self):
        """Details about the door log polling."""
        since = getattr(self.client, "door_log_status_since", None)
        return {
            "detail": getattr(self.client, "door_log_status_detail", None),
            "since": since.isoformat() if since else None,
            "poll_interval_seconds": getattr(self.client, "door_log_poll_interval", None),
            "next_attempt": (lambda t: t.isoformat() if t else None)(
                getattr(self.client, "door_log_next_attempt", None)),
            "failed_polls": getattr(self.client, "door_log_failures", 0),
        }


class AkuvoxLastDoorEventSensor(RestoreSensor):
    """The most recent door bell / door open event."""

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:door"

    EVENT_KEYS = {
        "CaptureType": "type",
        "Initiator": "initiator",
        "Location": "location",
        "RelayName": "relay",
        "CaptureTime": "capture_time",
        "PicUrl": "picture_url",
    }

    def __init__(self, client: AkuvoxApiClient, entry) -> None:
        """Initialize the last door event sensor."""
        self.client = client
        self._attr_name = "Akuvox last door event"
        self._attr_unique_id = f"{entry.entry_id}_last_door_event"
        self._attr_device_info = _door_log_device_info(entry)
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Restore the previous event and listen for new ones."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            self._attr_native_value = dt_util.parse_datetime(last_state.state)
            self._attr_extra_state_attributes = {
                key: last_state.attributes.get(key)
                for key in self.EVENT_KEYS.values()
                if key in last_state.attributes
            }
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_DOOR_LOG_UPDATE, self._handle_update))

    @callback
    def _handle_update(self) -> None:
        event = getattr(self.client, "last_door_event", None)
        event_time = getattr(self.client, "last_door_event_time", None)
        if not event or event_time is None or event_time == self._attr_native_value:
            return
        self._attr_native_value = event_time
        self._attr_extra_state_attributes = {
            name: event.get(key) for key, name in self.EVENT_KEYS.items() if key in event
        }
        if self._attr_extra_state_attributes.get("picture_url"):
            self._attr_entity_picture = self._attr_extra_state_attributes["picture_url"]
        self.async_write_ha_state()
