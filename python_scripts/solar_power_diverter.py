# python_scripts/solar_power_diverter.py

# Configuration Variables
# REPLACE THESE WITH YOUR ACTUAL HOME ASSISTANT ENTITY IDs
GRID_POWER_SENSOR = "sensor.power_grid" # Must be positive when importing, negative when exporting (or 0 when exporting)
PV_POWER_SENSOR = "sensor.power_photovoltaics"
VOLTAGE_SENSOR = "sensor.your_shelly_voltage_sensor" # e.g., sensor.shelly0110dimg3_xxxx_voltage

WATER_HEATER_MODE_SENSOR = "input_select.water_heater_mode"
WATER_HEATER_LIGHT_ENTITY = "light.your_shelly_dimmer_light" # e.g., light.shelly0110dimg3_xxxx_light_0
WH_POWER_CONSUMPTION_SENSOR = "sensor.your_shelly_power_sensor" # Optional monitoring

EMHASS_DEFERRABLE_SENSOR = "sensor.p_deferrable_water_heater"

SOLAR_DIVERSION_EXPORT_BUFFER_WATTS = 0.0 
MIN_PV_PRODUCTION_FOR_DIVERSION_WATTS = 250.0
WH_MAX_AMPS = 15.0 
WH_DIMMER_MIN_PCT_OUTPUT = 0.0
WH_DIMMER_MAX_PCT_OUTPUT = 85.0

PID_SETPOINT_WATTS = 0.0
PID_DT_SECONDS = 5.0

PID_KP_HIGH = 0.03 
PID_KI_HIGH = 0.005 
PID_KD_HIGH = 0.0
PID_KP_LOW = 0.01  
PID_KI_LOW = 0.002  
PID_KD_LOW = 0.0
PID_HIGH_LOW_SWITCH_ERROR_WATTS = 200.0 

PID_OUT_MIN_WATTS = 0.0 
PID_INTEGRAL_MIN = -500.0 
PID_INTEGRAL_HELPER = "input_number.pid_integral_term"
PID_PREVIOUS_ERROR_HELPER = "input_number.pid_previous_error"

# Helper Functions
def get_state_safe(entity_id_str):
    state_obj = hass.states.get(entity_id_str)
    if state_obj is None or state_obj.state in ['unknown', 'unavailable']:
        return None
    return state_obj.state

def get_float_state(entity_id_str, default=0.0):
    s = get_state_safe(entity_id_str)
    if s is not None:
        try:
            return float(s)
        except ValueError:
            pass
    return default

def calculate_wh_brightness(power_demand_watts, wh_max_power_dyn, min_pct, max_pct):
    if wh_max_power_dyn <= 0 or power_demand_watts <= 0: return min_pct
    clamped_demand_watts = min(power_demand_watts, wh_max_power_dyn)
    power_demand_percentage = (clamped_demand_watts / wh_max_power_dyn) * 100.0
    mapped_brightness = min_pct + (power_demand_percentage / 100.0) * (max_pct - min_pct)
    return round(max(min_pct, min(mapped_brightness, max_pct)))

# Main Script Logic
current_grid_watts = get_float_state(GRID_POWER_SENSOR, 0.0)
current_pv_watts = get_float_state(PV_POWER_SENSOR, 0.0)
current_voltage = get_float_state(VOLTAGE_SENSOR, 240.0)
wh_mode = get_state_safe(WATER_HEATER_MODE_SENSOR)

# Get EMHASS target ONLY if in Optim mode
emhass_target_watts = get_float_state(EMHASS_DEFERRABLE_SENSOR, 0.0) if wh_mode == 'Optim' else 0.0

if current_voltage <= 1.0:
    logger.error(f"WH PID Diverter: Voltage critical ({current_voltage}V). Forcing OFF.")
    hass.services.call('light', 'turn_off', {'entity_id': WATER_HEATER_LIGHT_ENTITY}, False)

elif wh_mode not in ['Solar', 'Optim']:
    # User manually turned it to OFF or BOOST. We don't interfere unless it's OFF and currently running.
    if wh_mode in ['OFF', 'Off', 'off']:
        hass.services.call('light', 'turn_on', {'entity_id': WATER_HEATER_LIGHT_ENTITY, 'brightness_pct': 0}, False)
        hass.services.call('light', 'turn_off', {'entity_id': WATER_HEATER_LIGHT_ENTITY}, False)

elif current_pv_watts < MIN_PV_PRODUCTION_FOR_DIVERSION_WATTS and emhass_target_watts <= 0:
    # No PV AND EMHASS doesn't want to heat. Turn off.
    hass.services.call('light', 'turn_on', {'entity_id': WATER_HEATER_LIGHT_ENTITY, 'brightness_pct': 0}, False)
    hass.services.call('input_number', 'set_value', {'entity_id': PID_INTEGRAL_HELPER, 'value': 0.0}, False)

else:
    # We have either PV excess OR EMHASS scheduled heating.
    wh_max_power_watts_dynamic = WH_MAX_AMPS * current_voltage
    pid_output_watts = 0.0

    # Only calculate PID if there is meaningful PV
    if current_pv_watts >= MIN_PV_PRODUCTION_FOR_DIVERSION_WATTS:
        previous_integral = get_float_state(PID_INTEGRAL_HELPER, 0.0)
        previous_error = get_float_state(PID_PREVIOUS_ERROR_HELPER, 0.0)

        error = PID_SETPOINT_WATTS - current_grid_watts
        if abs(error) > PID_HIGH_LOW_SWITCH_ERROR_WATTS:
            kp, ki, kd = PID_KP_HIGH, PID_KI_HIGH, PID_KD_HIGH
        else:
            kp, ki, kd = PID_KP_LOW, PID_KI_LOW, PID_KD_LOW

        integral = previous_integral + (ki * error * PID_DT_SECONDS)
        integral = max(PID_INTEGRAL_MIN, min(integral, wh_max_power_watts_dynamic * 1.2)) 
        
        derivative = kd * (error - previous_error) / PID_DT_SECONDS
        
        pid_output_watts_raw = (kp * error) + integral + derivative
        pid_output_watts = max(PID_OUT_MIN_WATTS, min(pid_output_watts_raw, wh_max_power_watts_dynamic))

        hass.services.call('input_number', 'set_value', {'entity_id': PID_INTEGRAL_HELPER, 'value': integral}, False)
        hass.services.call('input_number', 'set_value', {'entity_id': PID_PREVIOUS_ERROR_HELPER, 'value': error}, False)

    # The Merge: Take the max of PID (Solar) and EMHASS (Optim)
    wh_target_power_watts = max(pid_output_watts, emhass_target_watts)
    
    actual_wh_brightness_to_set = calculate_wh_brightness(
        wh_target_power_watts, wh_max_power_watts_dynamic, WH_DIMMER_MIN_PCT_OUTPUT, WH_DIMMER_MAX_PCT_OUTPUT
    )
    
    hass.services.call('light', 'turn_on', {'entity_id': WATER_HEATER_LIGHT_ENTITY, 'brightness_pct': actual_wh_brightness_to_set}, False)
    logger.info(f"WH Diverter: Target={wh_target_power_watts:.0f}W (PID={pid_output_watts:.0f}W, EMHASS={emhass_target_watts:.0f}W) -> Dimmer {actual_wh_brightness_to_set}%")
    
    if WH_POWER_CONSUMPTION_SENSOR:
        wh_actual_draw = get_float_state(WH_POWER_CONSUMPTION_SENSOR, 0.0)
        if get_state_safe(WH_POWER_CONSUMPTION_SENSOR) not in [None, 'unknown', 'unavailable']:
            logger.info(f"WH PID Diverter: Actual WH power (monitoring): {wh_actual_draw}W.")