import yaml
import numpy as np

data = {
    'hierarchies':[
        {
            'name': '2d_clean',
            'cases': [
                {
                    'name': 'NACA0012',
                    'meshes_folder_path': '../grids', # path to the folder containing mesh files
                    'mesh_files': ['naca0012_L0.cgns',], # List of mesh files intended for different levels of refinement
                    'geometry_info': {
                        'chordRef': 1.0,
                        'areaRef': 1.0,
                    },
                    'solver_parameters':{
                        # ANK Solver Parameters
                        "useANKSolver": True,
                        "nSubiterTurb": 20,
                        # NK Solver Parameters
                        "useNKSolver": False,
                        "NKSwitchTol": 1e-6,
                        # Coupled Solver Parameters
                        "ANKCoupledSwitchTol": 1e-3,
                        # Second order Solver Parameters
                        "ANKSecondOrdSwitchTol": 1e-12,
                        # Convergence Criterion
                        "L2Convergence": 1e-8,
                        "nCycles": 150000,
                    },
                    'exp_sets': [ 
                        {
                            'aoa_list': [0,5], # Info to construct array of aoa [start, end, interval]
                            'Re': 1e6, # Reynold's number
                            'mach': 0.7, # Mach number
                            'Temp': 298.0, # Temperature at which experiment was conducted in kelvin
                            'exp_data': 'exp_data/naca0012.txt', # Modify as required.
                        }
                    
                    ],
                },
            ],
        },
    ],
}
# Write to a YAML file
with open('naca0012_simInfo.yaml', 'w') as f:
    yaml.dump(data, f, sort_keys=False)