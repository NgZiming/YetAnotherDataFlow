from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .generate.pdf2vqa_formatter import MinerU2LLMInputOperator, LLMOutputParser, QA_Merger


else:
    import sys
    from dataflow.utils.registry import LazyLoader, generate_import_structure_from_type_checking



    cur_path = "dataflow/operators/pdf2vqa/"


    _import_structure = generate_import_structure_from_type_checking(__file__, cur_path)
    sys.modules[__name__] = LazyLoader(__name__, "dataflow/operators/pdf2vqa/", _import_structure)
