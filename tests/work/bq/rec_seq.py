# #########################################################################
# Copyright (c) 2022, UChicago Argonne, LLC. All rights reserved.         #
#                                                                         #
# Copyright 2022. UChicago Argonne, LLC. This software was produced       #
# under U.S. Government contract DE-AC02-06CH11357 for Argonne National   #
# Laboratory (ANL), which is operated by UChicago Argonne, LLC for the    #
# U.S. Department of Energy. The U.S. Government has rights to use,       #
# reproduce, and distribute this software.  NEITHER THE GOVERNMENT NOR    #
# UChicago Argonne, LLC MAKES ANY WARRANTY, EXPRESS OR IMPLIED, OR        #
# ASSUMES ANY LIABILITY FOR THE USE OF THIS SOFTWARE.  If software is     #
# modified to produce derivative works, such modified software should     #
# be clearly marked, so as not to confuse it with the version available   #
# from ANL.                                                               #
#                                                                         #
# Additionally, redistribution and use in source and binary forms, with   #
# or without modification, are permitted provided that the following      #
# conditions are met:                                                     #
#                                                                         #
#     * Redistributions of source code must retain the above copyright    #
#       notice, this list of conditions and the following disclaimer.     #
#                                                                         #
#     * Redistributions in binary form must reproduce the above copyright #
#       notice, this list of conditions and the following disclaimer in   #
#       the documentation and/or other materials provided with the        #
#       distribution.                                                     #
#                                                                         #
#     * Neither the name of UChicago Argonne, LLC, Argonne National       #
#       Laboratory, ANL, the U.S. Government, nor the names of its        #
#       contributors may be used to endorse or promote products derived   #
#       from this software without specific prior written permission.     #
#                                                                         #
# THIS SOFTWARE IS PROVIDED BY UChicago Argonne, LLC AND CONTRIBUTORS     #
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT       #
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS       #
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL UChicago     #
# Argonne, LLC OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,        #
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,    #
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;        #
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER        #
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT      #
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN       #
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE         #
# POSSIBILITY OF SUCH DAMAGE.                                             #
# #########################################################################

from tomocupy import utils
from tomocupy import logging
from tomocupy import conf_io
from tomocupy import tomo_functions
from threading import Thread
from queue import Queue
import cupy as cp
import numpy as np
import signal

__author__ = "Viktor Nikitin"
__copyright__ = "Copyright (c) 2022, UChicago Argonne, LLC."
__docformat__ = 'restructuredtext en'
__all__ = ['GPURec', ]


pinned_memory_pool = cp.cuda.PinnedMemoryPool()
cp.cuda.set_pinned_memory_allocator(pinned_memory_pool.malloc)

log = logging.getLogger(__name__)
        
class GPURec():
    '''
    Class for tomographic reconstruction on GPU with conveyor data processing by sinogram chunks (in z direction).
    Data reading/writing are done in separate threads, CUDA Streams are used to overlap CPU-GPU data transfers with computations.
    The implemented reconstruction method is Fourier-based with exponential functions for interpoaltion in the frequency domain (implemented with CUDA C).
    '''

    def __init__(self, args):
        # Set ^C, ^Z interrupt to abort and deallocate memory on GPU
        signal.signal(signal.SIGINT, utils.signal_handler)
        signal.signal(signal.SIGTSTP, utils.signal_handler)

        # configure sizes and output files
        cl_conf = conf_io.ConfIO(args)

        # chunks for processing
        self.shape_data_chunk = (cl_conf.nproj, cl_conf.ncz, cl_conf.ni)
        self.shape_recon_chunk = (cl_conf.ncz, cl_conf.n, cl_conf.n)
        self.shape_dark_chunk = (cl_conf.ndark, cl_conf.ncz, cl_conf.ni)
        self.shape_flat_chunk = (cl_conf.nflat, cl_conf.ncz, cl_conf.ni)

        # init tomo functions
        self.cl_tomo_func = tomo_functions.TomoFunctions(cl_conf)

        # streams for overlapping data transfers with computations
        self.stream1 = cp.cuda.Stream(non_blocking=False)
        self.stream2 = cp.cuda.Stream(non_blocking=False)
        self.stream3 = cp.cuda.Stream(non_blocking=False)
        
        #threads for data writing to disk
        self.write_threads = []
        for k in range(cl_conf.args.max_write_threads):
            self.write_threads.append(utils.WriteThread())            

        # queue for streaming projections
        self.data_queue = Queue()

        # additional refs
        self.cl_conf = cl_conf

    def recon_all(self):
        """Reconstruction of data from an h5file by splitting into sinogram chunks"""

        # start reading data to a queue
        # read_proc = Thread(
        #     target=self.cl_conf.read_data_to_queue, args=(self.data_queue,))
        # read_proc.start()

        # refs for faster access
        dtype = self.cl_conf.dtype
        in_dtype = self.cl_conf.in_dtype
        nzchunk = self.cl_conf.nzchunk
        lzchunk = self.cl_conf.lzchunk
        ncz = self.cl_conf.ncz
        
        # pinned memory for data item
        item_pinned = {}
        item_pinned['data'] = utils.pinned_array(
            np.zeros([2, *self.shape_data_chunk], dtype=in_dtype))
        item_pinned['dark'] = utils.pinned_array(
            np.zeros([2, *self.shape_dark_chunk], dtype=in_dtype))
        item_pinned['flat'] = utils.pinned_array(
            np.ones([2, *self.shape_flat_chunk], dtype=in_dtype))

        # gpu memory for data item
        item_gpu = {}
        item_gpu['data'] = cp.zeros(
            [2, *self.shape_data_chunk], dtype=in_dtype)
        item_gpu['dark'] = cp.zeros(
            [2, *self.shape_dark_chunk], dtype=in_dtype)
        item_gpu['flat'] = cp.ones(
            [2, *self.shape_flat_chunk], dtype=in_dtype)

        # pinned memory for reconstrution
        rec_pinned = utils.pinned_array(
            np.zeros([self.cl_conf.args.max_write_threads, *self.shape_recon_chunk], dtype=dtype))
        # gpu memory for reconstrution
        rec_gpu = cp.zeros([2, *self.shape_recon_chunk], dtype=dtype)
        
        log.info('Full reconstruction')
        # Conveyor for data cpu-gpu copy and reconstruction
        data0 = self.cl_conf.file_in['exchange/data']
        dark0 = self.cl_conf.file_in['exchange/data_dark']
        flat0 = self.cl_conf.file_in['exchange/data_white']

        
                
        for k in range(nzchunk+2):
            utils.printProgressBar(
                k, nzchunk+1, self.data_queue.qsize(), length=40)
            if k<nzchunk:
                item0 = {}
                st = self.cl_conf.args.start_row+k*self.cl_conf.ncz*2**self.cl_conf.args.binning
                end = self.cl_conf.args.start_row + \
                    (k*self.cl_conf.ncz+self.cl_conf.lzchunk[k])*2**self.cl_conf.args.binning
                item0['data'] = self.cl_conf.downsample(data0[:,  st:end, self.cl_conf.stn:self.cl_conf.endn])[
                    self.cl_conf.ids_proj]
                item0['flat'] = self.cl_conf.downsample(
                    flat0[:,  st:end, self.cl_conf.stn:self.cl_conf.endn])
                item0['dark'] = self.cl_conf.downsample(
                    dark0[:,  st:end, self.cl_conf.stn:self.cl_conf.endn])

                self.data_queue.put(item0)


            # self.cl_conf.read_data_to_queue(self.data_queue)
            if(k > 0 and k < nzchunk+1):
                with self.stream1:  # reconstruction
                    data = item_gpu['data'][(k-1) % 2]
                    dark = item_gpu['dark'][(k-1) % 2]
                    flat = item_gpu['flat'][(k-1) % 2]
                    rec = rec_gpu[(k-1) % 2]

                    data = self.cl_tomo_func.proc_sino(data, dark, flat)
                    data = self.cl_tomo_func.proc_proj(data)
                    data = cp.ascontiguousarray(data.swapaxes(0, 1))
                    sht = cp.tile(np.float32(0), data.shape[0])
                    data = self.cl_tomo_func.fbp_filter_center(data, sht)
                    self.cl_tomo_func.cl_rec.backprojection(
                        rec, data, self.stream1)

            if(k > 1):
                with self.stream1:  # gpu->cpu copy
                    # find free thread
                    ithread = 0                
                    while True:
                        if not self.write_threads[ithread].is_alive():
                            break                       
                        ithread=(ithread+1)%self.cl_conf.args.max_write_threads

                    rec_gpu[(k-2) % 2].get(out=rec_pinned[ithread])
            if(k < nzchunk):
                # copy to pinned memory
                item = self.data_queue.get()
                item_pinned['data'][k % 2, :, :lzchunk[k]] = item['data']
                item_pinned['dark'][k % 2, :, :lzchunk[k]] = item['dark']
                item_pinned['flat'][k % 2, :, :lzchunk[k]] = item['flat']
                with self.stream1:  # cpu->gpu copy
                    item_gpu['data'][k % 2].set(item_pinned['data'][k % 2])
                    item_gpu['dark'][k % 2].set(item_pinned['dark'][k % 2])
                    item_gpu['flat'][k % 2].set(item_pinned['flat'][k % 2])
            self.stream1.synchronize()
            if(k > 1):
                # add a new thread for writing to hard disk (after gpu->cpu copy is done)
                #self.write_threads[ithread].run(self.cl_conf.write_data,(rec_pinned[ithread], k-2))
                self.cl_conf.write_data(rec_pinned[ithread],k-2)
                
            self.stream1.synchronize()
            self.stream1.synchronize()

        for t in self.write_threads:
            t.join()

    def recon_try(self):
        """GPU reconstruction of 1 slice for different centers"""

        # read slice
        data, flat, dark = self.cl_conf.read_data_try()

        # copy to gpu
        data = cp.array(data)
        dark = cp.array(dark)
        flat = cp.array(flat)

        # preprocessing
        data = self.cl_tomo_func.proc_sino(data, dark, flat)
        data = self.cl_tomo_func.proc_proj(data)
        data = cp.ascontiguousarray(data.swapaxes(0, 1))

        # refs for faster access
        dtype = self.cl_conf.dtype
        nschunk = self.cl_conf.nschunk
        lschunk = self.cl_conf.lschunk
        ncz = self.cl_conf.ncz

        # pinned memory for reconstrution
        rec_pinned = utils.pinned_array(
            np.zeros([self.cl_conf.args.max_write_threads, *self.shape_recon_chunk], dtype=dtype))
        # gpu memory for reconstrution
        rec_gpu = cp.zeros([2, *self.shape_recon_chunk], dtype=dtype)

        # Conveyor for data cpu-gpu copy and reconstruction
        for k in range(nschunk+2):
            utils.printProgressBar(
                k, nschunk+1, self.data_queue.qsize(), length=40)
            if(k > 0 and k < nschunk+1):
                with self.stream2:  # reconstruction
                    sht = cp.pad(cp.array(self.cl_conf.shift_array[(
                        k-1)*ncz:(k-1)*ncz+lschunk[k-1]]), [0, ncz-lschunk[k-1]])
                    datat = cp.tile(data, [ncz, 1, 1])
                    datat = self.cl_tomo_func.fbp_filter_center(datat, sht)
                    self.cl_tomo_func.cl_rec.backprojection(
                        rec_gpu[(k-1) % 2], datat, self.stream2)
            if(k > 1):
                with self.stream3:  # gpu->cpu copy
                    # find free thread
                    ithread = 0                
                    while True:
                        if not self.write_threads[ithread].is_alive():
                            break                       
                        ithread=(ithread+1)%self.cl_conf.args.max_write_threads

                    rec_gpu[(k-2) % 2].get(out=rec_pinned[ithread])
            self.stream3.synchronize()
            if(k > 1):
                # add a new thread for writing to hard disk (after gpu->cpu copy is done)                
                for kk in range(lschunk[k-2]):
                    self.write_threads[ithread].run(self.cl_conf.write_data_try,(rec_pinned[ithread, kk],self.cl_conf.save_centers[(k-2)*ncz+kk]))
                    
            self.stream1.synchronize()
            self.stream2.synchronize()

        for t in self.write_threads:
            t.join()
