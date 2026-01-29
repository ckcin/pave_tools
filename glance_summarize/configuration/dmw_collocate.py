#!/usr/bin/env python

import glance.constants as constants

settings = {}
#settings[constants.WARN_MISSING_KEY] = True

defaultValues = {}
defaultValues[constants.FILL_VALUE_KEY] = -999

# lat/lon for DMW
lat_lon_info = {}
lat_lon_info[constants.LONGITUDE_NAME_KEY] = 'lon'
lat_lon_info[constants.LATITUDE_NAME_KEY] = 'lat'
lat_lon_info[constants.LON_LAT_EPSILON_KEY] = 0.0001

# variable list
setOfVariables = {}
# main list
setOfVariables['wind speed'] = { constants.VARIABLE_TECH_NAME_KEY: 'wind_speed', }
setOfVariables['wind direction'] = { constants.VARIABLE_TECH_NAME_KEY: 'wind_direction', }
setOfVariables['time'] = { constants.VARIABLE_TECH_NAME_KEY: 'time', }
setOfVariables['temperature'] = { constants.VARIABLE_TECH_NAME_KEY: 'temperature', }
setOfVariables['pressure'] = { constants.VARIABLE_TECH_NAME_KEY: 'pressure', }
setOfVariables['height'] = { constants.VARIABLE_TECH_NAME_KEY: 'height', }
# diag list
setOfVariables['forecast'] = { constants.VARIABLE_TECH_NAME_KEY: 'forecast', }
setOfVariables['u_component'] = { constants.VARIABLE_TECH_NAME_KEY: 'u_component', }
setOfVariables['v_component'] = { constants.VARIABLE_TECH_NAME_KEY: 'v_component', }
# pqi list
setOfVariables['direction_consistency_test'] = { constants.VARIABLE_TECH_NAME_KEY: 'direction_consistency_test', }
setOfVariables['forecast_consistency_test'] = { constants.VARIABLE_TECH_NAME_KEY: 'forecast_consistency_test', }
setOfVariables['local_consistency_test'] = { constants.VARIABLE_TECH_NAME_KEY: 'local_consistency_test', }
setOfVariables['speed_consistency_test'] = { constants.VARIABLE_TECH_NAME_KEY: 'speed_consistency_test', }
setOfVariables['vector_consistency_test'] = { constants.VARIABLE_TECH_NAME_KEY: 'vector_consistency_test', }
# location
setOfVariables['lon'] = { constants.VARIABLE_TECH_NAME_KEY: 'lon', }
setOfVariables['lat'] = { constants.VARIABLE_TECH_NAME_KEY: 'lat', }
