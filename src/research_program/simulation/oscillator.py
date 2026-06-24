from __future__ import annotations

from typing import List, Optional, Tuple

from research_program.simulation.coupling_functions import CouplingFuncType
import numpy as np

class Oscillator:
    def __init__(
        self,
        source_id: int,
        coupling_strength: int,
        strength_ratio:float,
        cycle_time: int,
        listening_rate: int,
        coupling_function: CouplingFuncType,
        event_type_enum,
    ) -> None:
        self.source_id = source_id

        self.coupling_strength = coupling_strength
        self.strength_ratio = strength_ratio
        self.cycle_time = cycle_time
        self.listening_rate = listening_rate
        self.coupling_function = coupling_function
        self.event_type_enum = event_type_enum

        self.active: bool = False

        self.send_count: int = 0
        self.sleep_count: int = 0
        self.awake_count: int = 0
        self.receive_count: int = 0

        self.last_event_time: Optional[float] = None
        self.last_received_from: Optional[int] = None
        self.last_send_time: Optional[float] = None
        self.current_awake_start_time: Optional[float] = None
        self.current_cycle_send_time: Optional[float] = None

        self.phase: float = 0.0
        self.phase_shift: float = 0.0

        self.received_times_in_awake: List[float] = []
        self.is_awake_window: bool = False


    def on_add(self, current_time: float) -> Tuple[object, float]:
        self.active = True
        self.last_event_time = current_time

        self.is_awake_window = True
        self.received_times_in_awake.clear()
        self.current_awake_start_time = current_time
        self.current_cycle_send_time = None

        next_time = current_time +  self.cycle_time * (self.listening_rate/2)/100
        return (self.event_type_enum.SEND_Event, next_time)

    def on_remove(self, current_time: float) -> None:
        
        self.active = False
        self.last_event_time = current_time
        self.is_awake_window = False
        self.current_awake_start_time = None
        self.current_cycle_send_time = None

    def on_send(
        self,
        current_time: float,
        phase_reference_time: Optional[float] = None,
    ) -> Tuple[object, float]:
        

        self.send_count += 1
        self.last_event_time = current_time
        self.last_send_time = current_time
        self.current_cycle_send_time = current_time if phase_reference_time is None else phase_reference_time

        next_time = current_time +  self.cycle_time * (self.listening_rate/2)/100
        return (self.event_type_enum.ASLEEP_Event, next_time)

    def on_skip_send(
        self,
        current_time: float,
        phase_reference_time: Optional[float] = None,
    ) -> Tuple[object, float]:
        self.last_event_time = current_time
        self.current_cycle_send_time = current_time if phase_reference_time is None else phase_reference_time

        next_time = current_time + self.cycle_time * (self.listening_rate / 2) / 100
        return (self.event_type_enum.ASLEEP_Event, next_time)

    def on_receive(self, sender_id: int, sender_phase: float, current_time: float) -> None:
        
        self.receive_count += 1
        self.last_event_time = current_time
        self.last_received_from = sender_id

        if self.active and self.is_awake_window:
            self.received_times_in_awake.append(current_time)

        


    def on_asleep(self, current_time: float) -> Tuple[object, float]:
        
        self.sleep_count += 1
        self.last_event_time = current_time

        self.is_awake_window = False
        self.current_awake_start_time = None

        if self.received_times_in_awake and self.current_cycle_send_time is not None:
            selected_receive_time = min(
                self.received_times_in_awake,
                key=lambda t: abs(t - self.current_cycle_send_time)
            )

            phase_diff = 2* np.pi*(selected_receive_time - self.current_cycle_send_time)/self.cycle_time
            coupling_value = self.coupling_function(phase_diff)

            next_awake_delay = coupling_value*self.coupling_strength*self.cycle_time*self.strength_ratio
        else:
            next_awake_delay = 0

        self.received_times_in_awake.clear()
        self.current_cycle_send_time = None

        

        next_time = current_time + self.cycle_time * (100 - self.listening_rate)/100 + next_awake_delay
        return (self.event_type_enum.AWAKE_Event, next_time)

    def on_awake(self, current_time: float) -> Tuple[object, float]:
       
        self.awake_count += 1
        self.last_event_time = current_time

        self.is_awake_window = True
        self.received_times_in_awake.clear()
        self.current_awake_start_time = current_time
        self.current_cycle_send_time = None

        next_time = current_time +  self.cycle_time * (self.listening_rate/2)/100
        return (self.event_type_enum.SEND_Event, next_time)
