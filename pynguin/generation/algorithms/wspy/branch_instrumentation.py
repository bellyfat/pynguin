# This file is part of Pynguin.
#
# Pynguin is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Pynguin is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Pynguin.  If not, see <https://www.gnu.org/licenses/>.
"""Provides capabilities to perform branch instrumentation."""
import inspect
from types import FunctionType, CodeType
from typing import Set

from bytecode import Instr, Bytecode  # type: ignore

from pynguin.generation.algorithms.wspy.tracking import ExecutionTracer
from pynguin.utils.iterator import ListIterator


class BranchInstrumentation:
    """Instruments modules/classes/methods/functions to enable branch distance tracking."""

    _INSTRUMENTED_FLAG: str = "instrumented"
    _TRACER_NAME: str = "tracer"

    def __init__(self, tracer: ExecutionTracer) -> None:
        self._predicate_id: int = 0
        self._function_id: int = 0
        self._tracer = tracer

    def instrument_function(self, to_instrument: FunctionType) -> None:
        """Adds branch distance instrumentation to the given function."""
        # Prevent multiple instrumentation
        assert not hasattr(
            to_instrument, BranchInstrumentation._INSTRUMENTED_FLAG
        ), "Function is already instrumented"
        setattr(to_instrument, BranchInstrumentation._INSTRUMENTED_FLAG, True)

        # install tracer in the globals of the function so we can call it from bytecode
        to_instrument.__globals__[self._TRACER_NAME] = self._tracer
        to_instrument.__code__ = self._instrument_code_recursive(to_instrument.__code__)

    def _instrument_code_recursive(self, code: CodeType) -> CodeType:
        """Instrument the given CodeType recursively."""
        # Nested functions are found within the consts of the CodeType.
        new_consts = []
        for const in code.co_consts:
            if hasattr(const, "co_code"):
                # The const is an inner function
                new_consts.append(self._instrument_code_recursive(const))
            else:
                new_consts.append(const)
        code = code.replace(co_consts=tuple(new_consts))

        instructions = Bytecode.from_code(code)
        code_iter: ListIterator = ListIterator(instructions)
        function_entered_inserted = False
        while code_iter.next():
            if not function_entered_inserted:
                self._add_function_entered(code_iter)
                function_entered_inserted = True
            current = code_iter.current()
            if isinstance(current, Instr) and current.is_cond_jump():
                if (
                    code_iter.has_previous()
                    and isinstance(code_iter.previous(), Instr)
                    and code_iter.previous().name == "COMPARE_OP"
                ):
                    self._add_cmp_predicate(code_iter)
                else:
                    self._add_bool_predicate(code_iter)
        return instructions.to_code()

    def _add_bool_predicate(self, iterator: ListIterator) -> None:
        self._tracer.predicate_exists(self._predicate_id)
        stmts = [
            Instr("DUP_TOP"),
            Instr("LOAD_GLOBAL", self._TRACER_NAME),
            Instr("LOAD_METHOD", ExecutionTracer.passed_bool_predicate.__name__),
            Instr("ROT_THREE"),
            Instr("ROT_THREE"),
            Instr("LOAD_CONST", self._predicate_id),
            Instr("CALL_METHOD", 2),
            Instr("POP_TOP"),
        ]
        iterator.insert_before(stmts)
        self._predicate_id += 1

    def _add_cmp_predicate(self, iterator: ListIterator) -> None:
        cmp_op = iterator.previous()
        self._tracer.predicate_exists(self._predicate_id)
        stmts = [
            Instr("DUP_TOP_TWO"),
            Instr("LOAD_GLOBAL", self._TRACER_NAME),
            Instr("LOAD_METHOD", ExecutionTracer.passed_cmp_predicate.__name__),
            Instr("ROT_FOUR"),
            Instr("ROT_FOUR"),
            Instr("LOAD_CONST", self._predicate_id),
            Instr("LOAD_CONST", cmp_op.arg),
            Instr("CALL_METHOD", 4),
            Instr("POP_TOP"),
        ]
        iterator.insert_before(stmts, 1)
        self._predicate_id += 1

    def _add_function_entered(self, iterator: ListIterator) -> None:
        self._tracer.function_exists(self._function_id)
        stmts = [
            Instr("LOAD_GLOBAL", self._TRACER_NAME),
            Instr("LOAD_METHOD", ExecutionTracer.entered_function.__name__),
            Instr("LOAD_CONST", self._function_id),
            Instr("CALL_METHOD", 1),
            Instr("POP_TOP"),
        ]
        iterator.insert_before(stmts)
        self._function_id += 1

    def instrument(self, obj, seen: Set = None) -> None:
        """
        Recursively instruments the given object and all functions within it.
        Technically there are a lot of different objects in Python that contain code,
        but we are only interested in functions, because methods are just wrappers around functions.
        See https://docs.python.org/3/library/inspect.html.

        There a also special objects for generators and coroutines that contain code,
        but these should not be of interest for us. If they should prove interesting,
        then a more sophisticated approach similar to dis.dis() should be adopted.
        """
        if not seen:
            seen = set()

        if obj in seen:
            return
        seen.add(obj)

        members = inspect.getmembers(obj)
        for (_, value) in members:
            if inspect.isfunction(value):
                self.instrument_function(value)
            if inspect.isclass(value) or inspect.ismethod(value):
                self.instrument(value, seen)