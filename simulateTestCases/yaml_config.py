from pydantic import BaseModel

class ref_sim_info(BaseModel):
    hierarchies: list[dict]

class ref_hierarchy_info(BaseModel):
    name: str
    cases: list

class ref_case_info(BaseModel):
    name: str
    meshes_folder_path: str
    mesh_files: list[str]
    geometry_info: dict
    solver_parameters: dict
    exp_sets: list

class ref_geometry_info(BaseModel):
    chordRef: float
    areaRef: float

class ref_exp_set_info(BaseModel):
    aoa_list: list[float]
    Re: float
    mach: float
    Temp: float
    exp_data: str=None