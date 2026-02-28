# tests/test_solar_diverter.py
import unittest
import os

class MockState:
    def __init__(self, state):
        self.state = str(state)

class MockStates:
    def __init__(self, states_dict):
        self._states = states_dict

    def get(self, entity_id):
        if entity_id in self._states:
            return MockState(self._states[entity_id])
        return None

class MockServices:
    def __init__(self):
        self.calls = []

    def call(self, domain, service, service_data, blocking=False):
        self.calls.append({'domain': domain, 'service': service, 'data': service_data})

class MockHass:
    def __init__(self, states_dict):
        self.states = MockStates(states_dict)
        self.services = MockServices()

class MockLogger:
    def info(self, msg): pass
    def error(self, msg): print(f"ERROR: {msg}")
    def warning(self, msg): pass

class TestSolarDiverter(unittest.TestCase):
    def setUp(self):
        # Path to the script we want to test
        script_path = os.path.join(os.path.dirname(__file__), '..', 'python_scripts', 'solar_power_diverter.py')
        with open(script_path, 'r') as f:
            self.script_code = f.read()

    def run_script(self, states_dict):
        hass_mock = MockHass(states_dict)
        logger_mock = MockLogger()
        
        # Define the sandbox environment
        env = {
            'hass': hass_mock,
            'logger': logger_mock,
            'round': round,
            'min': min,
            'max': max,
            'abs': abs,
            'float': float,
            'int': int
        }
        
        # Execute the script in the sandbox
        exec(self.script_code, env)
        return hass_mock

    def test_solar_mode_no_pv(self):
        states = {
            "input_select.water_heater_mode": "Solar",
            "sensor.power_photovoltaics": "100", # Below 250W threshold
            "sensor.power_grid": "500",
            "sensor.shelly0110dimg3_e4b3233b321c_light_0_voltage": "240"
        }
        hass = self.run_script(states)
        
        # Assert the script turned the dimmer to 0
        service_calls = hass.services.calls
        self.assertTrue(any(call['domain'] == 'light' and call['data'].get('brightness_pct') == 0 for call in service_calls))

    def test_optim_mode_night_heating(self):
        states = {
            "input_select.water_heater_mode": "Optim",
            "sensor.power_photovoltaics": "0",
            "sensor.p_deferrable_water_heater": "2000", # EMHASS scheduled 2000W
            "sensor.shelly0110dimg3_e4b3233b321c_light_0_voltage": "240"
        }
        hass = self.run_script(states)
        
        # Assert the script set brightness based on the 2000W EMHASS target
        service_calls = hass.services.calls
        brightness_calls = [c for c in service_calls if c['domain'] == 'light' and 'brightness_pct' in c['data']]
        self.assertTrue(len(brightness_calls) > 0)
        self.assertTrue(brightness_calls[0]['data']['brightness_pct'] > 0) # Should be ~55% for 2000W out of 3600W

if __name__ == '__main__':
    unittest.main()