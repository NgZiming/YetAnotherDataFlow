from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # agentic operators
    from .format_str_prompted_agentic_generator import FormatStrPromptedAgenticGenerator
    from .file_context import FileContextGenerator

else:
    import sys
    from dataflow.utils.registry import (
        LazyLoader,
        generate_import_structure_from_type_checking,
    )

    cur_path = "dataflow/operators/agentic/"

    _import_structure = generate_import_structure_from_type_checking(__file__, cur_path)
    sys.modules[__name__] = LazyLoader(
        __name__, "dataflow/operators/agentic/", _import_structure
    )
