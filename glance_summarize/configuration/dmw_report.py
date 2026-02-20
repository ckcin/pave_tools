#!/usr/bin/env python
# Glance configurate for reporting DMW differences

import glance.constants as constants

settings = {}
#settings[constants.WARN_MISSING_KEY]          = True
settings[constants.DO_MAKE_IMAGES_KEY]        = True
settings[constants.DO_MAKE_FORKS_KEY]         = True
settings[constants.DO_CLEAR_MEM_THREADED_KEY] = True

defaultValues = {}
defaultValues[constants.FILL_VALUE_KEY] = -999

# lat/lon for DMW
lat_lon_info = {}
lat_lon_info[constants.LONGITUDE_NAME_KEY] = 'lon-collocated'
lat_lon_info[constants.LATITUDE_NAME_KEY] = 'lat-collocated'
lat_lon_info[constants.LON_LAT_EPSILON_KEY] = 0.0001

# variable list
setOfVariables = {}
# main list
setOfVariables['wind_speed'] = { constants.VARIABLE_TECH_NAME_KEY: 'wind_speed-collocated', }
setOfVariables['wind_direction'] = { constants.VARIABLE_TECH_NAME_KEY: 'wind_direction-collocated', }
setOfVariables['time'] = { constants.VARIABLE_TECH_NAME_KEY: 'time-collocated', }
setOfVariables['temperature'] = { constants.VARIABLE_TECH_NAME_KEY: 'temperature-collocated', }
setOfVariables['pressure'] = { constants.VARIABLE_TECH_NAME_KEY: 'pressure-collocated', }
setOfVariables['height'] = { constants.VARIABLE_TECH_NAME_KEY: 'height-collocated', }
# diag list
setOfVariables['forecast'] = { constants.VARIABLE_TECH_NAME_KEY: 'forecast-collocated', }
setOfVariables['u_component_of_vector1'] = { constants.VARIABLE_TECH_NAME_KEY: 'u_component_of_vector1-collocated', }
setOfVariables['v_component_of_vector1'] = { constants.VARIABLE_TECH_NAME_KEY: 'v_component_of_vector1-collocated', }
setOfVariables['u_component_of_vector2'] = { constants.VARIABLE_TECH_NAME_KEY: 'u_component_of_vector2-collocated', }
setOfVariables['v_component_of_vector2'] = { constants.VARIABLE_TECH_NAME_KEY: 'v_component_of_vector2-collocated', }
setOfVariables['vertical_wind_shear'] = { constants.VARIABLE_TECH_NAME_KEY: 'vertical_wind_shear-collocated', }
# pqi list
setOfVariables['direction_consistency_test'] = { constants.VARIABLE_TECH_NAME_KEY: 'direction_consistency_test-collocated', }
setOfVariables['forecast_consistency_test'] = { constants.VARIABLE_TECH_NAME_KEY: 'forecast_consistency_test-collocated', }
setOfVariables['local_consistency_test'] = { constants.VARIABLE_TECH_NAME_KEY: 'local_consistency_test-collocated', }
setOfVariables['speed_consistency_test'] = { constants.VARIABLE_TECH_NAME_KEY: 'speed_consistency_test-collocated', }
setOfVariables['vector_consistency_test'] = { constants.VARIABLE_TECH_NAME_KEY: 'vector_consistency_test-collocated', }

# vector plotting
setOfVariables['Wind Vectors, colored by Air Pressure'] = {
    constants.VARIABLE_TECH_NAME_KEY: 'pressure-collocated',
    constants.MAGNITUDE_VAR_NAME_KEY: 'wind_speed-collocated',
    constants.DIRECTION_VAR_NAME_KEY: 'wind_direction-collocated',
}
