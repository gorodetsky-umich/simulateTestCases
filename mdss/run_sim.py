import os
import sys
import pandas as pd
import numpy as np
import yaml
import time
import copy
import subprocess
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from datetime import date, datetime

from mphys.multipoint import Multipoint
from mphys.scenario_aerodynamic import ScenarioAerodynamic
from adflow.mphys import ADflowBuilder
from baseclasses import AeroProblem
import openmdao.api as om
from mpi4py import MPI

from mdss.helpers import load_yaml_file, load_csv_data, check_input_yaml, write_python_file, write_job_script, run_as_subprocess

comm = MPI.COMM_WORLD

################################################################################
# Setting up default ADflow Options
################################################################################
default_aero_options = {
    # Print Options
    "printIterations": False,
    "printAllOptions": False,
    "printIntro": False,
    "printTiming": False,
    # I/O Parameters
    "gridFile": f"grids/naca0012_L1.cgns", # Default grid file
    "outputDirectory": ".",
    "monitorvariables": ["resrho", "resturb", "cl", "cd", "yplus"],
    "writeTecplotSurfaceSolution": True,
    # Physics Parameters
    "equationType": "RANS",
    "liftindex": 3,  # z is the lift direction
    # Solver Parameters
    "smoother": "DADI",
    "CFL": 0.5,
    "CFLCoarse": 0.25,
    "MGCycle": "sg",
    "MGStartLevel": -1,
    "nCyclesCoarse": 250,
    # ANK Solver Parameters
    "useANKSolver": True,
    "nsubiterturb": 5,
    "anksecondordswitchtol": 1e-4,
    "ankcoupledswitchtol": 1e-6,
    "ankinnerpreconits": 2,
    "ankouterpreconits": 2,
    "anklinresmax": 0.1,
    # Termination Criteria
    "L2Convergence": 1e-12,
    "L2ConvergenceCoarse": 1e-2,
    "nCycles": 75000,
}

################################################################################
# Multipoint class definition
################################################################################

class Top(Multipoint):

    """
    Sets up an OpenMDAO problem using MPhys and ADflow for aerodynamic simulations.

    This class is designed to integrate OpenMDAO with MPhys and ADflow to perform aerodynamic simulations. It sets up the problem environment, manages inputs and outputs, and configures scenarios for simulation.

    Methods
    --------
    **setup()**
        Initializes and sets up the required subsystems and scenarios.

    **configure()**
        Configures the aerodynamic problem (e.g., reference area, chord, angle of attack) and connects design variables to the system.

    Inputs
    -------
    - **case_info** : dict
        Dictionary containing geometry and configuration details for the case being analyzed.
    - **exp_info** : dict
        Dictionary with experimental conditions such as Mach number, Reynolds number, and temperature.
    - **aero_options** : dict
        ADflow solver parameters that control aerodynamic analysis.

    Outputs
    --------
    None. This class directly modifies the OpenMDAO problem structure to include aerodynamic analysis subsystems.

    """

    def __init__(self, case_info, exp_info, aero_options):
        super().__init__()
        self.case_info = case_info
        self.exp_info = exp_info
        self.aero_options = aero_options

    def setup(self):
                        
        adflow_builder = ADflowBuilder(self.aero_options, scenario="aerodynamic")
        adflow_builder.initialize(self.comm)
        adflow_builder.err_on_convergence_fail = True

        ################################################################################
        # MPHY setup
        ################################################################################

        # ivc to keep the top level DVs
        self.add_subsystem("dvs", om.IndepVarComp(), promotes=["*"])

        # create the mesh and cruise scenario because we only have one analysis point
        self.add_subsystem("mesh", adflow_builder.get_mesh_coordinate_subsystem())
        self.mphys_add_scenario("cruise", ScenarioAerodynamic(aero_builder=adflow_builder))
        self.connect("mesh.x_aero0", "cruise.x_aero")

    def configure(self):
        aoa = 0.0 # Set default Angle of attack

        geometry_info = self.case_info['geometry_info'] # Load geometry info
        chordRef = geometry_info['chordRef']
        areaRef = geometry_info['areaRef']

        ap0 = AeroProblem(
            name="ap0",
            # Experimental Conditions 
            mach = self.exp_info['mach'], reynolds=self.exp_info['Re'], reynoldsLength=chordRef, T=self.exp_info['Temp'], 
            alpha=aoa,
            # Geometry Info
            areaRef=areaRef, 
            chordRef=chordRef, 
            evalFuncs=["cl", "cd"]
        )
        ap0.addDV("alpha", value=aoa, name="aoa", units="deg")


        # set the aero problem in the coupling and post coupling groups
        self.cruise.coupling.mphys_set_ap(ap0)
        self.cruise.aero_post.mphys_set_ap(ap0)

        # add dvs to ivc and connect
        self.dvs.add_output("aoa", val=aoa, units="deg")
        self.connect("aoa", ["cruise.coupling.aoa", "cruise.aero_post.aoa"])

class run_sim():
    """
    Executes ADflow simulations using the `Top` class.

    This class sets up, runs, and post-processes aerodynamic simulations based on input parameters provided via a YAML configuration file. It validates the input, manages directories, and handles outputs, including plots and summary files.

    Methods
    -------
    **run_problem()**
        Sets up and runs the OpenMDAO problem for all cases, hierarchies, and refinement levels.

    **run()**
        Executes the simulation on either a local machine or an HPC system.

    **post_process()**
        Generates plots comparing experimental data (if available) with ADflow simulation results.

    Inputs
    ----------
    - **info_file** : str
        Path to the YAML file containing simulation configuration and information.
    """

    def __init__(self, info_file):
        # Validate the input yaml file
        check_input_yaml(info_file)
        if comm.rank == 0:
            print(f"{'-' * 50}")
            print("YAML file validation is successful")
            print(f"{'-' * 50}")

        self.info_file = info_file
        self.sim_info = load_yaml_file(self.info_file, comm)
        self.out_dir = self.sim_info['out_dir']
        self.final_out_file = f"{self.out_dir}/overall_sim_info.yaml" # Setting the overall simulation info file.
        

        # Create the output directory if it doesn't exist
        if not os.path.exists(self.out_dir): 
            if comm.rank == 0:
                os.makedirs(self.out_dir)
    
    ################################################################################
    # Code for running simulations
    ################################################################################   
    def run_problem(self):
        """
        Sets up and runs the OpenMDAO problem for aerodynamic simulations.

        This method iterates through all hierarchies, cases, refinement levels, and angles of attack defined in the input YAML file. For each combination, it sets up the OpenMDAO problem, runs the simulation, and stores the results.

        Outputs
        -------
        - **A CSV file**:
            Contains results for each angle of attack at the current refinement level.
        - **A YAML file**:
            Stores simulation data for each angle of attack in the corresponding directory.
        - **A final YAML file**:
            Summarizes all simulation results across hierarchies, cases, and refinement levels.

        Notes
        -----
        This method ensures that:

        - Existing successful simulations are skipped.
        - Directories are created dynamically if they do not exist.
        - Simulation results are saved in structured output files.
        """

        # Store a copy of input YAML file in output directory
        input_yaml_file = f"{self.out_dir}/input_file.yaml"
        if comm.rank == 0:
            with open(input_yaml_file, 'w') as input_yaml_handle:
                yaml.dump(self.sim_info, input_yaml_handle, sort_keys=False)
        
        sim_info_copy = copy.deepcopy(self.sim_info) # Copying to run the loop
        sim_out_info = copy.deepcopy(self.sim_info) # Copying to write the output YAML file
        start_time = time.time()
        start_wall_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for hierarchy, hierarchy_info in enumerate(sim_info_copy['hierarchies']): # loop for Hierarchy level

            for case, case_info in enumerate(hierarchy_info['cases']): # loop for cases in hierarchy

                aero_options = default_aero_options.copy()
                aero_options.update(case_info['solver_parameters']) # Update ADflow solver parameters

                for exp_set, exp_info in enumerate(case_info['exp_sets']): # loop for experimental datasets that may present

                    if comm.rank == 0:
                        print(f"{'#' * 30}")
                        print(f"{'SIMULATION INFO':^30}")
                        print(f"{'#' * 30}")
                        print(f"{'Hierarchy':<20}: {hierarchy_info['name']}")
                        print(f"{'Case Name':<20}: {case_info['name']}")
                        print(f"{'Experimental Condition':<20}: {exp_set}")
                        print(f"{'Reynolds Number (Re)':<20}: {exp_info['Re']}")
                        print(f"{'Mach Number':<20}: {exp_info['mach']}")
                        print(f"{'=' * 30}")
                    
                    # Extract the Angle of attacks for which the simulation has to be run
                    aoa_list = exp_info['aoa_list']

                    exp_sim_info = {} # Creating experimental level sim info dictionary for overall sim info file

                    for ii, mesh_file in enumerate(case_info['mesh_files']): # Loop for refinement levels

                        refinement_level = f"L{ii}"
                        CLList = []
                        CDList = []
                        TList = []
                        FList = [] # Fail flag list

                        refinement_level_dict = {} # Creating refinement level sim info dictionary for overall sim info file

                        # Update Grid file
                        aero_options['gridFile'] = f"{case_info['meshes_folder_path']}/{mesh_file}"

                
                        for aoa in aoa_list: # loop for angles of attack
                            
                            # Date
                            current_date = date.today()
                            date_string = current_date.strftime("%Y-%m-%d")

                            # Define output directory -- Written to store in the parent directory
                            output_dir = f"{self.out_dir}/{hierarchy_info['name']}/{case_info['name']}/exp_set_{exp_set}/{refinement_level}/aoa_{aoa}"
                            aero_options['outputDirectory'] = output_dir

                            # name of the simulation info file at the aoa level directory
                            aoa_info_file = f"{output_dir}/aoa_{aoa}.yaml" 


                            aoa_level_dict = {} # Creating aoa level sim info dictionary for overall sim info file

                            # Call 'run_as_subprocess' function if the user has requested
                            if sim_info_copy['run_as_subprocess'] == 'yes':
                                run_as_subprocess(sim_info_copy, hierarchy_info, case_info, exp_info, aoa, output_dir, sim_info_copy['nproc'], comm)

                            ################################################################################
                            # OpenMDAO setup
                            ################################################################################

                            os.environ["OPENMDAO_REPORTS"]="0" # Do this to disable report generation by OpenMDAO

                            prob = om.Problem()
                            prob.model = Top(case_info, exp_info, aero_options)

                            # Checking for existing sucessful simualtion info, 
                            if os.path.exists(output_dir):
                                try:
                                    with open(aoa_info_file, 'r') as aoa_file:
                                        aoa_sim_info = yaml.safe_load(aoa_file)
                                    fail_flag = aoa_sim_info['fail_flag']
                                    if fail_flag == 0:
                                        CLList.append(aoa_sim_info['cl'])
                                        CDList.append(aoa_sim_info['cd'])
                                        TList.append(float(aoa_sim_info['wall_time'].replace(" sec", "")))
                                        FList.append(fail_flag)

                                        # To Store in the overall simulation out file in case of skipping
                                        aoa_level_dict = {
                                            'cl': float(aoa_sim_info['cl']),
                                            'cd': float(aoa_sim_info['cd']),
                                            'wall_time': aoa_sim_info['wall_time'],
                                            'fail_flag': int(fail_flag),
                                            'out_dir': output_dir,
                                        }
                                        refinement_level_dict[f"aoa_{aoa}"] = aoa_level_dict

                                        if comm.rank == 0:
                                            print(f"{'-'*50}")
                                            print(f"{'NOTICE':^50}")
                                            print(f"{'-'*50}")
                                            print(f"Skipping Angle of Attack (AoA): {float(aoa):<5} | Reason: Existing successful simulation found")
                                            print(f"{'-'*50}")
                                        continue # Continue to next loop if there exists a successful simulation
                                except:
                                    fail_flag = 1
                            elif not os.path.exists(output_dir): # Create the directory if it doesn't exist
                                if comm.rank == 0:
                                    os.makedirs(output_dir)

                            
                            if comm.rank == 0:
                                print(f"{'-'*50}")
                                print(f"Starting Angle of Attack (AoA): {float(aoa):<5}")
                                print(f"{'-'*50}")
                            # Setup the problem
                            prob.setup()

                            # Set the angle
                            prob["aoa"] = float(aoa)

                            om.n2(prob, show_browser=False, outfile=f"{output_dir}/mphys_aero.html")

                            # Run the model
                            aoa_start_time = time.time() # Stote the start time
                            try:
                                prob.run_model()
                                fail_flag = 0
                            except:
                                fail_flag = 1
                                
                            aoa_end_time = time.time() # Store the end time
                            aoa_run_time = aoa_end_time - aoa_start_time # Compute the run time

                            prob.model.list_inputs(units=True)
                            prob.model.list_outputs(units=True)
                    
                            # Store a Yaml file at this level
                            aoa_out_dic = {
                                'case': case_info['name'],
                                'exp_info': exp_info,
                                'mesh_file_used': f"{case_info['meshes_folder_path']}/{mesh_file}",
                                'AOA': float(aoa),
                                'cl': float(prob["cruise.aero_post.cl"][0]),
                                'cd': float(prob["cruise.aero_post.cd"][0]),
                                'refinement_level': refinement_level,
                                'wall_time': f"{aoa_run_time:.2f} sec",
                                'fail_flag': int(fail_flag),
                                'out_dir': output_dir,
                            }
                            with open(aoa_info_file, 'w') as interim_out_yaml:
                                yaml.dump(aoa_out_dic, interim_out_yaml, sort_keys=False)
                        
                            # To Store in the overall simulation out file
                            aoa_level_dict = {
                                'cl': float(prob["cruise.aero_post.cl"][0]),
                                'cd': float(prob["cruise.aero_post.cd"][0]),
                                'wall_time': f"{aoa_run_time:.2f} sec",
                                'fail_flag': int(fail_flag),
                                'out_dir': output_dir,
                            }
                            refinement_level_dict[f"aoa_{aoa}"] = aoa_level_dict

                            # Adding cl, cd, wall time, Fail flags to their respective lists to create the csv file at refinement level
                            CLList.append(float(prob["cruise.aero_post.cl"][0]))
                            CDList.append(float(prob["cruise.aero_post.cd"][0]))
                            TList.append(aoa_run_time)
                            FList.append(fail_flag)
                        
                        
                        # Write simulation results to a csv file
                        refinement_level_data = {
                            "Alpha": [f"{alpha:6.2f}" for alpha in aoa_list],
                            "CL": [f"{cl:8.4f}" for cl in CLList],
                            "CD": [f"{cd:8.4f}" for cd in CDList],
                            "FFlag": [f"{int(FF):12f}" for FF in FList],
                            "WTime": [f"{wall_time:10.2f}" for wall_time in TList]
                        }

                        # Define the output file path
                        refinement_level_dir = os.path.dirname(output_dir)
                        ADflow_out_file = f"{refinement_level_dir}/ADflow_output.csv"
                        
                        df = pd.DataFrame(refinement_level_data) # Create a panda DataFrame
                        # Write the DataFrame to a CSV file
                        df.to_csv(ADflow_out_file, index=False)

                        # Add csv file location to the overall simulation out file
                        refinement_level_dict['csv_file'] = ADflow_out_file
                        refinement_level_dict['refinement_out_dir'] = refinement_level_dir

                        # Add refinement level dict to exp level dict
                        exp_sim_info[f"{refinement_level}"] = refinement_level_dict

                    # Add experimental level simulation to the overall simulation out file
                    exp_out_dir = os.path.dirname(refinement_level_dir)
                    exp_sim_info['exp_set_out_dir'] = exp_out_dir
                    sim_out_info['hierarchies'][hierarchy]['cases'][case]['exp_sets'][exp_set]['sim_info'] = exp_sim_info

        end_time = time.time()
        end_wall_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        net_run_time = end_time - start_time
        sim_out_info['overall_sim_info'] = {
            'start_time': start_wall_time,
            'end_time': end_wall_time,
            'total_wall_time': f"{net_run_time:.2f} sec"
        }

        # Store the final simulation out file.
        if comm.rank == 0:
            with open(self.final_out_file, 'w') as final_out_yaml_handle:
                yaml.dump(sim_out_info, final_out_yaml_handle, sort_keys=False)
        comm.Barrier()
    
    ################################################################################
    # Code for user to run simulations
    ################################################################################
    def run(self):
        """
        Executes the simulation on either a local machine or an HPC system.

        This method checks the simulation settings from the input YAML file. Based on the `hpc` flag, it either runs the simulation locally or generates an HPC job script for execution.

        Notes
        -----
        - For local execution (`hpc: no`), it directly calls `run_problem()`.
        - For HPC execution (`hpc: yes`), it creates a Python file and a job script, then submits the job using `sbatch`.
        """
        sim_info_copy = copy.deepcopy(self.sim_info)
        if sim_info_copy['hpc'] == "no":
            self.run_problem()
        elif sim_info_copy['hpc'] == "yes":
            python_file_path = f"{self.out_dir}/run_sim.py"
            slrum_out_file = f"overall_sim_out.txt"
            # Create a python file to run
            write_python_file(python_file_path)
            # Create a job script to run
            job_script_path = write_job_script(sim_info_copy, self.out_dir, slrum_out_file, python_file_path, self.info_file)

            subprocess.run(["sbatch", job_script_path])
            
    ################################################################################
    # Code for Post Processing
    ################################################################################
    
    def post_process(self):
        """
        Generates plots comparing experimental data with ADflow simulation results.

        This method creates comparison plots for each experimental condition and refinement level. The plots include `CL` (Lift Coefficient) and `CD` (Drag Coefficient) against the angle of attack (Alpha). Experimental data, if provided, is included in the plots for validation.

        Outputs
        -------
        - *PNG plots*:
            Stored in the experimental condition directory for each hierarchy and case.

        Notes
        -----
        - Experimental data is optional. If not provided, only simulation results are plotted.
        - Plots are saved with clear labels and legends for easy interpretation.
        """
        sim_out_info = load_yaml_file(self.final_out_file, comm)

        for hierarchy, hierarchy_info in enumerate(sim_out_info['hierarchies']): # loop for Hierarchy level
            for case, case_info in enumerate(hierarchy_info['cases']): # loop for cases in hierarchy
                for exp_set, exp_info in enumerate(case_info['exp_sets']): # loop for experimental datasets that may present
                    
                    # Plot setup
                    fig, axs = plt.subplots(1, 2, figsize=(14, 6))
                    fig.suptitle('Comparison between ADflow Simulation and Experimental Data')

                    # Load Experimental Data
                    try:
                        exp_data = load_csv_data(exp_info['exp_data'], comm)
                    except:
                        if comm.rank == 0:
                            print(f"Warning: Experimental data location is not specified or the data is not readable.")
                        exp_data = None

                    if exp_data is not None: # Only plot if data loaded successfully
                        exp_data.columns = exp_data.columns.str.strip()  # Clean column names
    
                        # Convert to numeric to avoid plotting issues
                        exp_data['Alpha'] = pd.to_numeric(exp_data['Alpha'], errors='coerce')
                        exp_data['CL'] = pd.to_numeric(exp_data['CL'], errors='coerce')
                        exp_data['CD'] = pd.to_numeric(exp_data['CD'], errors='coerce')
                        exp_data = exp_data.dropna()  # Drop rows with missing data
                        
                        axs[0].plot(exp_data['Alpha'], exp_data['CL'], label='Experimental', color='black', linestyle='--', marker='o')
                        axs[1].plot(exp_data['Alpha'], exp_data['CD'], label='Experimental', color='black', linestyle='--', marker='o')
                        
                    else:
                        if comm.rank == 0:
                            print("Continuing to plot without experimental data.")

                    num_levels = len(case_info['mesh_files'])  # Total refinement levels
                    colors = cm.viridis(np.linspace(0, 1, num_levels))  # Generate unique colors for each level
                    # Load Simulated Data
                    exp_out_dir = exp_info['sim_info']['exp_set_out_dir']
                    sim_data = {}
                    for ii, mesh_file in enumerate(case_info['mesh_files']): # Loop for refinement levels
                        refinement_level_dir = f"{exp_out_dir}/L{ii}"
                        ADflow_out_file = f"{refinement_level_dir}/ADflow_output.csv"
                        sim_data = load_csv_data(ADflow_out_file, comm)
                        if sim_data is not None:  # Only plot if data loaded successfully
                            label = f"L{ii}"
                            axs[0].plot(sim_data['Alpha'], sim_data['CL'], label=label, color=colors[ii], linestyle='-', marker='s') # Plot CL vs Alpha for this refinement level
                            axs[1].plot(sim_data['Alpha'], sim_data['CD'], label=label, color=colors[ii], linestyle='-', marker='s') # Plot CD vs Alpha for this refinement level
                    
                    # Setting titles, labels, and legends
                    axs[0].set_title('$C_L$ vs Alpha')
                    axs[0].set_xlabel('Alpha (deg)')
                    axs[0].set_ylabel('$C_L$')
                    axs[0].legend()
                    axs[0].grid(True)

                    axs[1].set_title('$C_D$ vs Alpha')
                    axs[1].set_xlabel('Alpha (deg)')
                    axs[1].set_ylabel('$C_D$')
                    axs[1].legend()
                    axs[1].grid(True)

                    plt.tight_layout(rect=[0, 0, 1, 0.96])  # Adjust layout to fit title
                    plt.savefig(f"{exp_out_dir}/ADflow_Results.png")