import copy
from simulateTestCases.run_sim import run_sim
from simulateTestCases.helpers import load_yaml_file, load_csv_data
from mpi4py import MPI
comm = MPI.COMM_WORLD

def get_sim_data(info_file, run_flag=0):
    """
    Generates a dictionary containing simulation data organized hierarchically.

    This function processes a YAML file with simulation information and creates a
    nested dictionary (`sim_data`) with details about simulation hierarchies, cases,
    experiment sets, refinement levels, and angles of attack. It also provides an
    option to run a simulation if the required data does not exist.

    Inputs:
    ----------
    - **info_file** : str
        Path to the input YAML file containing simulation information or configuration.
    - **run_flag** : int (0 or 1), optional
        Flag to determine behavior if required simulation data is not found:
        - `0` (default): Exit without running the simulation.
        - `1`: Run the simulation and populate the data.

    Outputs:
    -------
    **sim_data**: dict
        A dictionary contating simulation data.
    """
    info = load_yaml_file(info_file, comm)
    sim_data = {} # Initiating a dictionary to store simulation data

    try:  # Check if the file is overall sim info file and stores the simulation info
        overall_sim_info = info["overall_sim_info"]
        sim_info = copy.deepcopy(info)

    except KeyError:  # if the file is input info file, loads the overall_sim_info.yaml if the simulation is run already
        try:
            sim_info = load_yaml_file(f"{info['out_dir']}/overall_sim_info.yaml")
        except FileNotFoundError:
            if comm.rank == 0:
                print(f"No existing simulation found in the specified output directory: {info['out_dir']}")
            if run_flag == 1:
                if comm.rank == 0:
                    print("Continuing to run simulation")
                sim = run_sim(info_file)
                sim.run()
                sim_info = load_yaml_file(f"{info['out_dir']}/overall_sim_info.yaml")
            elif run_flag == 0:
                if comm.rank == 0:
                    print("Exiting")
                return sim_data
        
    
    # Loop through hierarchy levels
    for hierarchy_index, hierarchy_info in enumerate(sim_info['hierarchies']):
        hierarchy_name = hierarchy_info['name']
        if hierarchy_name not in sim_data:
            sim_data[hierarchy_name] = {}

        # Loop through cases in the hierarchy
        for case_index, case_info in enumerate(hierarchy_info['cases']):
            case_name = case_info['name']
            if case_name not in sim_data[hierarchy_name]:
                sim_data[hierarchy_name][case_name] = {}

            # Loop through experiment sets in the case
            for exp_index, exp_info in enumerate(case_info['exp_sets']):
                exp_set_key = f"exp_set_{exp_index}"
                if exp_set_key not in sim_data[hierarchy_name][case_name]:
                    sim_data[hierarchy_name][case_name][exp_set_key] = {}

                # Loop through mesh files
                for ii, mesh_file in enumerate(case_info['mesh_files']):
                    refinement_level = f"L{ii}"
                    if refinement_level not in sim_data[hierarchy_name][case_name][exp_set_key]:
                        sim_data[hierarchy_name][case_name][exp_set_key][refinement_level] = {}

                    # Loop through angles of attack
                    for aoa in exp_info['aoa_list']:
                        aoa_key = f"aoa_{aoa}"
                        cl = exp_info['sim_info'][refinement_level][aoa_key].get("cl")
                        cd = exp_info['sim_info'][refinement_level][aoa_key].get("cd")

                        # Populate the dictionary
                        if aoa_key not in sim_data[hierarchy_name][case_name][exp_set_key][refinement_level]:
                            sim_data[hierarchy_name][case_name][exp_set_key][refinement_level][aoa_key] = {}

                        sim_data[hierarchy_name][case_name][exp_set_key][refinement_level][aoa_key]['cl'] = cl
                        sim_data[hierarchy_name][case_name][exp_set_key][refinement_level][aoa_key]['cd'] = cd

    return sim_data


        


