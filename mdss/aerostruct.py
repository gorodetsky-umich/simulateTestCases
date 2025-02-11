import os
import time
import numpy as np
import yaml
import shutil

from mphys.multipoint import Multipoint
from mphys.scenario_aerostructural import ScenarioAeroStructural
from mphys.scenario_aerodynamic import ScenarioAerodynamic
from mpi4py import MPI
from adflow.mphys import ADflowBuilder
from baseclasses import AeroProblem
from tacs.mphys import TacsBuilder
from funtofem.mphys import MeldBuilder

import openmdao.api as om
from mpi4py import MPI

from mdss.helpers import load_yaml_file
from mdss.tacs_setup import tacs_setup

comm = MPI.COMM_WORLD

class Top(Multipoint):

    def __init__(self, sim_info):
        super().__init__()
        self.sim_info = sim_info

    def setup(self):

        ################################################################################
        # ADflow Setup
        ################################################################################
        aero_builder = ADflowBuilder(self.sim_info['aero_options'], scenario="aerostructural")
        aero_builder.initialize(self.comm)
        self.add_subsystem("mesh_aero", aero_builder.get_mesh_coordinate_subsystem())

        if self.sim_info['problem'] == 'AeroStructural': # TACS setup only for aerostructural scenario
            ################################################################################
            # TACS Setup
            ################################################################################
            tacs_out_dir = '.' # Temporary output directory for TACS outptuts
            tacs_config = tacs_setup(self.sim_info['structural_properties'], self.sim_info['load_info'], self.sim_info['tacs_out_dir'])

            struct_builder = TacsBuilder(mesh_file=self.sim_info['struct_mesh_fpath'], element_callback=tacs_config.element_callback,
                                        problem_setup=tacs_config.problem_setup)
            struct_builder.initialize(self.comm)
            ndv_struct = struct_builder.get_ndv()

            self.add_subsystem("mesh_struct", struct_builder.get_mesh_coordinate_subsystem())

            ################################################################################
            # Transfer Scheme Setup
            ################################################################################

            isym = 1  # y-symmetry
            ldxfer_builder = MeldBuilder(aero_builder, struct_builder, isym=isym)
            ldxfer_builder.initialize(self.comm)

            ################################################################################
            # MPHYS Setup for AeroStructural Problem
            ################################################################################

            # ivc to keep the top level DVs
            dvs = self.add_subsystem("dvs", om.IndepVarComp(), promotes=["*"])

            dvs.add_output("dv_struct", np.array(ndv_struct * [0.002]))

            scenario = "cruise"

            ################# Get the following inputs from the user #############################################
            nonlinear_solver = om.NonlinearBlockGS(maxiter=25, iprint=2, use_aitken=True, rtol=1e-14, atol=1e-14)
            linear_solver = om.LinearBlockGS(maxiter=25, iprint=2, use_aitken=True, rtol=1e-14, atol=1e-14)
            ######################################################################################################
            self.mphys_add_scenario(
                scenario,
                ScenarioAeroStructural(
                    aero_builder=aero_builder, struct_builder=struct_builder, ldxfer_builder=ldxfer_builder
                ),
                nonlinear_solver,
                linear_solver,
            )

            for discipline in ["aero", "struct"]:
                self.mphys_connect_scenario_coordinate_source("mesh_%s" % discipline, scenario, discipline)

            self.connect("dv_struct", f"{scenario}.dv_struct")
        
        elif self.sim_info['problem'] == 'Aerodynamic':
            ################################################################################
            # MPHY setup for Aero Problem
            ################################################################################

            # ivc to keep the top level DVs
            self.add_subsystem("dvs", om.IndepVarComp(), promotes=["*"])

            # create the mesh and cruise scenario because we only have one analysis point
            self.add_subsystem("mesh", aero_builder.get_mesh_coordinate_subsystem())
            self.mphys_add_scenario("cruise", ScenarioAerodynamic(aero_builder=aero_builder))
            self.connect("mesh.x_aero0", "cruise.x_aero")
    
    def configure(self): # Set Angle of attack
        aoa = 0.0 # Set Angle of attack. Will be changed when running the problem
        ap = AeroProblem(
            name="cruise",
            # Experimental Conditions 
            mach = self.sim_info['mach'], reynolds=self.sim_info['Re'], reynoldsLength=self.sim_info['chordRef'], T=self.sim_info['Temp'],
            # Geometry Info
            alpha=aoa, 
            areaRef=self.sim_info['areaRef'],
            chordRef=self.sim_info['chordRef'],
            evalFuncs=["cl", "cd"],
        )
        ap.addDV("alpha", value=aoa, name="aoa", units="deg")

        if self.sim_info['problem'] == 'AeroStructural':
            self.cruise.coupling.aero.mphys_set_ap(ap)
            variable_to_connect = "cruise.coupling.aero.aoa"
        elif self.sim_info['problem'] == 'Aerodynamic':
            self.cruise.coupling.mphys_set_ap(ap)
            variable_to_connect = "cruise.coupling.aoa"
        self.cruise.aero_post.mphys_set_ap(ap)

        # define the aero DVs in the IVC
        self.dvs.add_output("aoa", val=aoa, units="deg")

        # connect to the aero for each scenario
        self.connect("aoa", [variable_to_connect, "cruise.aero_post.aoa"])

def run_problem(case_info_fpath, exp_info_fpath, ref_level_dir, aoa_csv_str, aero_grid_fpath, struct_mesh_fpath=None):
    # Extarct the required info
    case_info = load_yaml_file(case_info_fpath, comm)
    exp_info = load_yaml_file(exp_info_fpath, comm)
    aoa_list = [float(x) for x in aoa_csv_str.split(',')]

    problem = case_info['problem']
    aero_options = case_info['aero_options']
    geo_info = case_info['geo_info']


    # Update Grid file
    aero_options['gridFile'] = aero_grid_fpath

    # Initialize the structrual info dictionaries which remains empty for aerodynamic problems
    structural_properties = {}
    laod_info = {}
    solver_options = {}
    
    if problem == 'AeroStructural': # Update structural info with user given data for aerostructural problem
        structural_properties.update(case_info['structural_properties'])
        laod_info.update(case_info['load_info'])
        solver_options.update(case_info['solver_options'])
        
    sim_info = {
            'problem': problem,
            'aero_options': aero_options,
            'chordRef': geo_info['chordRef'],
            'areaRef': geo_info['areaRef'],
            'mach': exp_info['mach'],
            'Re': exp_info['Re'],
            'Temp': exp_info['Temp'],
            
            # Add Structural Info
            'tacs_out_dir': ref_level_dir,
            'struct_mesh_fpath': struct_mesh_fpath,
            'structural_properties': structural_properties,
            'load_info': laod_info,
            'solver_options': solver_options,
        }
    
    ################################################################################
    # OpenMDAO setup
    ################################################################################

    os.environ["OPENMDAO_REPORTS"]="0" # Do this to disable report generation by OpenMDAO
    prob = om.Problem()
    prob.model = Top(sim_info)

    # Setup the problem
    prob.setup()

    for aoa in aoa_list:
        aoa_out_dir = f"{ref_level_dir}/aoa_{aoa}" # name of the aoa output directory
        aoa_info_file = f"{aoa_out_dir}/aoa_{aoa}.yaml" # name of the simulation info file at the aoa level directory
        
        aoa_out_dir = os.path.abspath(aoa_out_dir)

        #os.environ['OPENMDAO_WORKDIR'] = os.path.abspath(aoa_out_dir)
        ################################################################################
        # Checking for existing sucessful simualtion info
        ################################################################################ 
        if os.path.exists(aoa_out_dir):
            try:
                with open(aoa_info_file, 'r') as aoa_file:
                    aoa_sim_info = yaml.safe_load(aoa_file)
                fail_flag = aoa_sim_info['fail_flag']
                if fail_flag == 0:
                    if comm.rank == 0:
                        print(f"{'-'*50}")
                        print(f"{'NOTICE':^50}")
                        print(f"{'-'*50}")
                        print(f"Skipping Angle of Attack (AoA): {float(aoa):<5} | Reason: Existing successful simulation found")
                        print(f"{'-'*50}")
                    continue # Continue to next loop if there exists a successful simulation
            except:
                fail_flag = 1
        elif not os.path.exists(aoa_out_dir): # Create the directory if it doesn't exist
            if comm.rank == 0:
                os.makedirs(aoa_out_dir)
        
        ################################################################################
        # Run sim when a succesful simulation is not found
        ################################################################################
        fail_flag = 0

        # Change output directory in openMDAO instance
        if problem == 'AeroStructural':
            print(f"{'+'*50}")
            print(f"{'+'*50}")
            print(prob.model.cruise.coupling.struct.sp.options['defaults'])
            print(prob.model.cruise.coupling.aero.options['solver'].options['outputDirectory'])
            prob.model.cruise.coupling.aero.options['solver'].options['outputDirectory'] = aoa_out_dir
            prob.model.cruise.coupling.struct.sp.setOption('outputdir', aoa_out_dir)
            print(prob.model.cruise.coupling.aero.options['solver'].options['outputDirectory'])
            print(prob.model.cruise.coupling.struct.sp.options['outputdir'])
            print(f"{'+'*50}")
            print(f"{'+'*50}")

        elif problem == 'Aerodynamic':
            prob.model.cruise.coupling.options['solver'].options['outputDirectory'] = aoa_out_dir
        

        prob["aoa"] = float(aoa) # Set Angle of attack

        # Run the model
        aoa_start_time = time.time() # Store the start time
        try:
            prob.run_model()
            fail_flag = 1
        except:
            fail_flag = 1
        
        # Manually move the '.f5' files to the respective aoa_out_dir
        if comm.rank == 0:
            for file in os.listdir(ref_level_dir):
                src_file = os.path.join(ref_level_dir, file)
                dest_file = os.path.join(aoa_out_dir, file)
                if file.endswith(".f5"):  # Identify TACS output files
                    shutil.move(src_file, dest_file)

        aoa_end_time = time.time() # Store the end time
        aoa_run_time = aoa_end_time - aoa_start_time # Compute the run time

        prob.model.list_inputs(units=True)
        prob.model.list_outputs(units=True)

        # Store a Yaml file at this level
        aoa_out_dic = {
            # 'refinement_level': refinement_level, # Decide on this later
            'case':case_info['name'],
            'problem': problem,
            'aero_mesh_fpath': aero_grid_fpath,
            'exp_info': exp_info,
            'AOA': float(aoa),
            'cl': float(prob["cruise.aero_post.cl"][0]),
            'cd': float(prob["cruise.aero_post.cd"][0]),
            'wall_time': f"{aoa_run_time:.2f} sec",
            'fail_flag': int(fail_flag),
            'out_dir': aoa_out_dir,
        }
        if problem == 'AeroStructural': # Add structural info
            struct_info_dict = {
                'struct_mesh_fpath': struct_mesh_fpath,
                'structural_properties': structural_properties,
                'load_info': laod_info,
                'solver_options': solver_options
            }
            aoa_out_dic.update(struct_info_dict)
        with open(aoa_info_file, 'w') as interim_out_yaml:
            yaml.dump(aoa_out_dic, interim_out_yaml, sort_keys=False)


