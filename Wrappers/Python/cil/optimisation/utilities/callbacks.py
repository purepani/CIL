#  Copyright 2024 United Kingdom Research and Innovation
#  Copyright 2024 The University of Manchester
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
# Authors:
# - CIL Developers, listed at: https://github.com/TomographicImaging/CIL/blob/master/NOTICE.txt


from abc import ABC, abstractmethod
from functools import partialmethod

from tqdm.auto import tqdm as tqdm_auto
from tqdm.std import tqdm as tqdm_std
import numpy as np


class Callback(ABC):
    '''Base Callback to inherit from for use in :code:`Algorithm.run(callbacks: list[Callback])`.

    Parameters
    ----------
    verbose: int, choice of 0,1,2, default 1
        0=quiet, 1=info, 2=debug.
    '''
    def __init__(self, verbose=1):
        self.verbose = verbose

    @abstractmethod
    def __call__(self, algorithm):
        pass


class _OldCallback(Callback):
    '''Converts an old-style :code:`def callback` to a new-style :code:`class Callback`.

    Parameters
    ----------
    callback: :code:`callable(iteration, objective, x)`
    '''
    def __init__(self, callback, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.func = callback

    def __call__(self, algorithm):
        if algorithm.update_objective_interval > 0 and algorithm.iteration % algorithm.update_objective_interval == 0:
            self.func(algorithm.iteration, algorithm.get_last_objective(return_all=self.verbose>=2), algorithm.x)


class ProgressCallback(Callback):
    ''':code:`tqdm`-based progress bar.

    Parameters
    ----------
    tqdm_class: default :code:`tqdm.auto.tqdm`
    **tqdm_kwargs:
        Passed to :code:`tqdm_class`.
    '''
    def __init__(self, verbose=1, tqdm_class=tqdm_auto, **tqdm_kwargs):
        super().__init__(verbose=verbose)
        self.tqdm_class = tqdm_class
        self.tqdm_kwargs = tqdm_kwargs
        self._obj_len = 0  # number of objective updates

    def __call__(self, algorithm):
        if not hasattr(self, 'pbar'):
            tqdm_kwargs = self.tqdm_kwargs
            tqdm_kwargs.setdefault('total', algorithm.max_iteration)
            tqdm_kwargs.setdefault('disable', not self.verbose)
            tqdm_kwargs.setdefault('initial', max(0, algorithm.iteration))
            self.pbar = self.tqdm_class(**tqdm_kwargs)
        if (obj_len := len(algorithm.objective)) != self._obj_len:
            self.pbar.set_postfix(algorithm.objective_to_dict(self.verbose>=2), refresh=False)
            self._obj_len = obj_len
        self.pbar.update(algorithm.iteration - self.pbar.n)


class _TqdmText(tqdm_std):
    ''':code:`tqdm`-based progress but text-only updates on separate lines.

    Parameters
    ----------
    num_format: str
        Format spec for postfix numbers (i.e. objective values).
    bar_format: str
        Passed to :code:`tqdm`.
    '''
    def __init__(self, *args, num_format='+8.3e', bar_format="{n:>6d}/{total_fmt:<6} {rate_fmt:>9}{postfix}", **kwargs):
        self.num_format = num_format
        super().__init__(*args, bar_format=bar_format, mininterval=0, maxinterval=0, position=0, **kwargs)
        self._instances.remove(self)  # don't interfere with external progress bars

    @staticmethod
    def status_printer(file):
        fp_flush = getattr(file, 'flush', lambda: None)

        def fp_write(s):
            file.write(f"{s}\n")
            fp_flush()

        return fp_write

    def format_num(self, n):
        return f'{n:{self.num_format}}'

    def display(self, *args, **kwargs):
        """
        Clears :code:`postfix` if :code:`super().display()` succeeds
        (if display updates are more frequent than objective updates, users should not think the objective has stabilised).
        """
        if (updated := super().display(*args, **kwargs)):
            self.set_postfix_str('', refresh=False)
        return updated


class TextProgressCallback(ProgressCallback):
    ''':code:`ProgressCallback` but printed on separate lines to screen.

    Parameters
    ----------
    miniters: int, default :code:`Algorithm.update_objective_interval`
        Number of algorithm iterations between screen prints.
    '''
    __init__ = partialmethod(ProgressCallback.__init__, tqdm_class=_TqdmText)

    def __call__(self, algorithm):
        if not hasattr(self, 'pbar'):
            self.tqdm_kwargs['miniters'] = min((
                self.tqdm_kwargs.get('miniters', algorithm.update_objective_interval),
                algorithm.update_objective_interval))
        return super().__call__(algorithm)


class LogfileCallback(TextProgressCallback):
    ''':code:`TextProgressCallback` but to a file instead of screen.

    Parameters
    ----------
    log_file: FileDescriptorOrPath
        Passed to :code:`open()`.
    mode: str
        Passed to :code:`open()`.
    '''
    def __init__(self, log_file, mode='a', **kwargs):
        self.fd = open(log_file, mode=mode)
        super().__init__(file=self.fd, **kwargs)
        
class EarlyStoppingObjectiveValue(Callback):
    '''Callback that stops iterations if the change in the objective value is less than a provided threshold value.

    Parameters
    ----------
    threshold: float, default 1e-6 

    Note
    -----
    This callback only compares the last two calculated objective values. If `update_objective_interval` is greater than 1, the objective value is not calculated at each iteration (which is the default behaviour), only every `update_objective_interval` iterations.
    
    '''
    def __init__(self, threshold=1e-6):
        self.threshold=threshold
    
    
    def __call__(self, algorithm):
        if len(algorithm.loss)>=2:
            if np.abs(algorithm.loss[-1]-algorithm.loss[-2])<self.threshold:
                raise StopIteration
                
class CGLSEarlyStopping(Callback):
    '''Callback to work with CGLS. It causes the algorithm to terminate if  :math:`||A^T(Ax-b)||_2 < \epsilon||A^T(Ax_0-b)||_2` where `epsilon` is set to default as '1e-6', :math:`x` is the current iterate and :math:`x_0` is the initial value. 
    It will also terminate if the algorithm begins to diverge i.e. if :math:`||x||_2> \omega`, where `omega` is set to default as 1e6. 
    
    Parameters
    ----------
    epsilon: float, default 1e-6 
        Usually a small number: the algorithm to terminate if :math:`||A^T(Ax-b)||_2 < \epsilon||A^T(Ax_0-b)||_2`
    omega: float, default 1e6 
        Usually a large number: the algorithm will terminate if  :math:`||x||_2> \omega`
        
    Note
    -----
    This callback is implemented to replicate the automatic behaviour of CGLS in CIL versions <=24. It also replicates the behaviour of https://web.stanford.edu/group/SOL/software/cgls/. 
    '''
    def __init__(self, epsilon=1e-6, omega=1e6):
        self.epsilon=epsilon
        self.omega=omega
    
    
    def __call__(self, algorithm):
        
        if (algorithm.norms <= algorithm.norms0 * self.epsilon):
            print('The norm of the residual is less than {} times the norm of the initial residual and so the algorithm is terminated'.format(self.epsilon))
            raise StopIteration
        self.normx = algorithm.x.norm()
        if algorithm.normx >= self.omega:
            print('The norm of the solution is greater than {} and so the algorithm is terminated'.format(self.omega))
            raise StopIteration
            
        
