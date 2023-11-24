"""Load OpenBB functions at OpenAI tools for function calling in Langchain"""
import inspect

from typing import get_args, get_origin, Literal, Modu
from types import ModuleType

from langchain.tools import StructuredTool
from openbb import obb
from pydantic.v1 import create_model, BaseModel
from pydantic.v1.fields import FieldInfo
from pydantic_core import PydanticUndefinedType


def _fetch_obb_module(openbb_command_root: str) -> ModuleType:
    module_path_split = openbb_command_root.split("/")[1:]
    module_path = '.'.join(module_path_split)

    # Iteratively get module
    module = obb
    for attr in module_path.split('.'):
        module = getattr(module, attr)

    return module

def _fetch_schemas(openbb_command_root: str) -> dict:
    # Ugly hack to make it compatiable with the look-up (even though we convert
    # it back) so that we have a nicer API for the user.
    module_root_path = openbb_command_root.replace("/", ".")
    schemas = {
        k.replace('.', '/'): v for k, v in obb.coverage.command_model.items() if module_root_path in k
    }
    return schemas

def _fetch_callables(openbb_command_root):
    module = _fetch_obb_module(openbb_command_root)
    members = inspect.getmembers(module)
    members_dict = {x[0]: x[1] for x in members if '__' not in x[0] and '_run' not in x[0]}

    schemas = _fetch_schemas(openbb_command_root)

    # Create callables dict, with the same key as used in the schemas
    callables = {}
    for k in schemas.keys():
        try:
            callables[k] = members_dict[k.split('/')[-1]] 
        except KeyError:  # Sometimes we don't have a specific callable for an endpoint, so we skip.
            pass
    return callables

def _fetch_outputs(schema):
    outputs = []
    output_fields = schema['openbb']['Data']['fields']
    for name, t in output_fields.items():
        if isinstance(t.annotation, type):
            type_str = t.annotation.__name__
        else:
            type_str = str(t.annotation).replace("typing.", "")
        outputs.append((name, type_str))
    return outputs


def from_schema_to_pydantic_model(model_name, schema):
    create_model_kwargs = {}
    for field, field_info in schema.items():

        field_type = field_info.annotation

        # Handle default values
        if not isinstance(field_info.default, PydanticUndefinedType):
            field_default_value = field_info.default
            new_field_info = FieldInfo(  # Weird hack, because of how the default field value works
                description=field_info.description,
                default=field_default_value,
            )
        else:
            new_field_info = FieldInfo(
                description=field_info.description,
            )
        create_model_kwargs[field] = (field_type, new_field_info)
    return create_model(model_name, **create_model_kwargs)


def return_results(func):
    """Return the results rather than the OBBject."""
    def wrapper_func(*args, **kwargs):
        return func(*args, **kwargs).results
    return wrapper_func


def from_openbb_to_langchain_func(openbb_callable, openbb_schema):
    func_name = openbb_callable.__name__
    func_schema = openbb_schema['openbb']['QueryParams']['fields']
    
    pydantic_model = from_schema_to_pydantic_model(
        model_name=f'{func_name}InputModel',
        schema=func_schema
    )

    outputs = _fetch_outputs(openbb_schema)
    description = openbb_callable.__doc__.split("\n")[0]
    description += "\nThe following data is available:\n\n"
    description += ", ".join(e[0] for e in outputs)

    tool = StructuredTool(
        name = func_name,
        func=return_results(openbb_callable),
        description=description,
        args_schema=pydantic_model
    )

    return tool


def map_openbb_functions_to_langchain_tools(schemas_dict, callables_dict):
    tools = []
    for route in callables_dict.keys():
        tool = from_openbb_to_langchain_func(
            openbb_callable=callables_dict[route],
            openbb_schema=schemas_dict[route]
        )
        tools.append(tool)
    return tools


def map_openbb_collection_to_langchain_tools(openbb_command_root: str) -> list[StructuredTool]:
    """Map a collection of OpenBB callables from a commad root to Langchain StructuredTools.

    Examples
    --------
    >>> fundamental_tools = map_openbb_collection_to_langchain_tools("/equity/fundamental")
    >>> crypto_price_tools = map_openbb_collection_to_langchain_tools("/crypto/price")


    """
    schemas = _fetch_schemas(openbb_command_root)
    callables = _fetch_callables(openbb_command_root)
    tools = map_openbb_functions_to_langchain_tools(
        schemas_dict=schemas,
        callables_dict=callables
    )
    return tools