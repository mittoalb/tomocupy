#!/usr/bin/env python
# -*- coding: utf-8 -*-

# *************************************************************************** #
#                  Copyright © 2022, UChicago Argonne, LLC                    #
#                           All Rights Reserved                               #
#                         Software Name: Tomocupy                             #
#                     By: Argonne National Laboratory                         #
#                                                                             #
#                           OPEN SOURCE LICENSE                               #
#                                                                             #
# Redistribution and use in source and binary forms, with or without          #
# modification, are permitted provided that the following conditions are met: #
#                                                                             #
# 1. Redistributions of source code must retain the above copyright notice,   #
#    this list of conditions and the following disclaimer.                    #
# 2. Redistributions in binary form must reproduce the above copyright        #
#    notice, this list of conditions and the following disclaimer in the      #
#    documentation and/or other materials provided with the distribution.     #
# 3. Neither the name of the copyright holder nor the names of its            #
#    contributors may be used to endorse or promote products derived          #
#    from this software without specific prior written permission.            #
#                                                                             #
#                                                                             #
# *************************************************************************** #
#                               DISCLAIMER                                    #
#                                                                             #
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS         #
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT           #
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS           #
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT    #
# HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,      #
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED    #
# TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR      #
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF      #
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING        #
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS          #
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.                #
# *************************************************************************** #

''' Paganin phase retrieval implementation 
copied from tomopy
'''

import numpy as np
import cupy as cp
from cupy.fft import fft2, ifft2
from cupyx.scipy.ndimage import median_filter

BOLTZMANN_CONSTANT = 1.3806488e-16  # [erg/k]
SPEED_OF_LIGHT = 299792458e+2  # [cm/s]
PI = 3.14159265359
PLANCK_CONSTANT = 6.58211928e-19  # [keV*s]


def _wavelength(energy):
    return 2 * PI * PLANCK_CONSTANT * SPEED_OF_LIGHT / energy


def paganin_filter(
        data, pixel_size=1e-4, dist=50, energy=20, alpha=1e-3, filt='paganin', db=1000, pad=True):
    """
    Perform single-step phase retrieval from phase-contrast measurements
    :cite:`Paganin:02`.

    Parameters
    ----------
    tomo : ndarray
        3D tomographic data.
    pixel_size : float, optional
        Detector pixel size in cm.
    dist : float, optional
        Propagation distance of the wavefront in cm.
    energy : float, optional
        Energy of incident wave in keV.
    alpha : float, optional
        Regularization parameter for Paganin method.
    db : float, optional
    	delta/beta for generalized Paganin phase retrieval 
    theta : float
    	phase dimple size for Z paganin method
    gamma : int, positive   
        regularization for Z paganin method
    pad : bool, optional
        If True, extend the size of the projections by padding with zeros.
    Returns
    -------
    ndarray
        Approximated 3D tomographic phase data.
    """
    # New dimensions and pad value after padding.
    py, pz, val = _calc_pad(data, pixel_size, dist, energy, pad)

    # Compute the reciprocal grid.
    dx, dy, dz = data.shape
    if filt == 'paganin':
    	w2 = _reciprocal_grid(pixel_size, dy + 2 * py, dz + 2 * pz)
    elif filt == 'Gpaganin':
    	print('frequency')
        kf = _reciprocal_gridG(pixel_size, dy + 2 * py, dz + 2 * pz)
        
        
    # Filter in Fourier space.
    if filt == 'paganin':
    	phase_filter = cp.fft.fftshift(
        	_paganin_filter_factor(energy, dist, alpha, w2))
    elif filt == 'Gpaganin':
    	print('phase')
        phase_filter = cp.fft.fftshift(
        	_paganin_filter_factorG(energy, dist, kf, pixel_size, db))

    prj = cp.full((dy + 2 * py, dz + 2 * pz), val, dtype=data.dtype)

    return data




def _retrieve_phase(data, phase_filter, px, py, prj, pad):
    dx, dy, dz = data.shape
    num_jobs = data.shape[0]
    normalized_phase_filter = phase_filter / phase_filter.max()

    for m in range(num_jobs):
        prj[px:dy + px, py:dz + py] = data[m]
        prj[:px] = prj[px]
        prj[-px:] = prj[-px-1]
        prj[:, :py] = prj[:, py][:, cp.newaxis]
        prj[:, -py:] = prj[:, -py-1][:, cp.newaxis]
        fproj = fft2(prj)
        fproj *= normalized_phase_filter
        proj = cp.real(ifft2(fproj))
        if pad:
            proj = proj[px:dy + px, py:dz + py]
        data[m] = proj


def _calc_pad(data, pixel_size, dist, energy, pad):
    """
    Calculate new dimensions and pad value after padding.

    Parameters
    ----------
    data : ndarray
        3D tomographic data.
    pixel_size : float
        Detector pixel size in cm.
    dist : float
        Propagation distance of the wavefront in cm.
    energy : float
        Energy of incident wave in keV.
    pad : bool
        If True, extend the size of the projections by padding with zeros.

    Returns
    -------
    int
        Pad amount in projection axis.
    int
        Pad amount in sinogram axis.
    float
        Pad value.
    """
    dx, dy, dz = data.shape
    wavelength = _wavelength(energy)
    py, pz, val = 0, 0, 0
    if pad:
        val = _calc_pad_val(data)
        py = _calc_pad_width(dy, pixel_size, wavelength, dist)
        pz = _calc_pad_width(dz, pixel_size, wavelength, dist)

    return py, pz, val


def _paganin_filter_factor(energy, dist, alpha, w2):
    """
    	Original Paganing phase retrieval
    	Paganin et al 2002
    """
    return 1 / (_wavelength(energy) * dist * w2 / (4 * PI) + alpha)
    
def _paganin_filter_factorG(energy, dist, kf, pixel_size, db):
    """
    	Generalized phase retrieval method
    	Paganin et al 2020
    """
    alpha = db*(dist*_wavelength(energy))/(4*PI)
    return 1 / (1.0 -(2*alpha/pixel_size**2)*(kf-2))
    
def _calc_pad_width(dim, pixel_size, wavelength, dist):
    pad_pix = cp.ceil(PI * wavelength * dist / pixel_size ** 2)
    return int((pow(2, cp.ceil(cp.log2(dim + pad_pix))) - dim) * 0.5)


def _calc_pad_val(data):
    return cp.mean((data[..., 0] + data[..., -1]) * 0.5)


def _reciprocal_grid(pixel_size, nx, ny):
    """
    Calculate reciprocal grid.

    Parameters
    ----------
    pixel_size : float
        Detector pixel size in cm.
    nx, ny : int
        Size of the reciprocal grid along x and y axes.

    Returns
    -------
    ndarray
        Grid coordinates.
    """
    # Sampling in reciprocal space.
    indx = _reciprocal_coord(pixel_size, nx)
    indy = _reciprocal_coord(pixel_size, ny)
    np.square(indx, out=indx)
    np.square(indy, out=indy)

    # there is no substitute for np.add.outer using cupy.
    #return cp.array(np.add.outer(indx, indy))
    return np.add.outer(indx, indy)


def _reciprocal_gridG(pixel_size, nx, ny):
    """
    Calculate reciprocal grid for Generalized Paganin method.

    Parameters
    ----------
    pixel_size : float
        Detector pixel size in cm.
    nx, ny : int
        Size of the reciprocal grid along x and y axes.

    Returns
    -------
    ndarray
        Grid coordinates.
    """
    # Sampling in reciprocal space.
    indx = np.cos(_reciprocal_coord(pixel_size, nx)*pixel_size)
    indy = np.cos(_reciprocal_coord(pixel_size, ny)*pixel_size)
    
    return np.add.outer(indx, indy)


def _reciprocal_coord(pixel_size, num_grid):
    """
    Calculate reciprocal grid coordinates for a given pixel size
    and discretization.

    Parameters
    ----------
    pixel_size : float
        Detector pixel size in cm.
    num_grid : int
        Size of the reciprocal grid.

    Returns
    -------
    ndarray
        Grid coordinates.
    """
    n = num_grid - 1
    rc = np.arange(-n, num_grid, 2, dtype=cp.float32)
    rc *= 0.5 / (n * pixel_size)
    return rc

