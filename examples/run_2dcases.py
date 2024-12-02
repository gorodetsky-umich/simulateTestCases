from simulateTestCases.run_sim import run_sim

output_dir = 'output'
sim = run_sim('simInfo.yaml', output_dir) # Input the simulation info and outptu dir
sim.check_yaml_file() # Check the yaml_file
sim.run_problem() # Run the simulation
sim.post_process() # Genrates plots comparing experimental data and simulated data and stores them