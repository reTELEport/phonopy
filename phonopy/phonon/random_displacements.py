# Copyright (C) 2018 Atsushi Togo
# All rights reserved.
#
# This file is part of phonopy.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# * Redistributions of source code must retain the above copyright
#   notice, this list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in
#   the documentation and/or other materials provided with the
#   distribution.
#
# * Neither the name of the phonopy project nor the names of its
#   contributors may be used to endorse or promote products derived
#   from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import numpy as np
from phonopy.harmonic.dynmat_to_fc import get_commensurate_points_in_integers
from phonopy.structure.brillouin_zone import get_qpoints_in_Brillouin_zone
from phonopy.units import VaspToTHz, THzToEv, Kb, Hbar, AMU, EV, Angstrom, THz


class RandomDisplacements(object):
    """Generate Random displacements by Canonical ensenmble.

    Attributes
    ----------
    u : ndarray
        Random atomic displacements generated by canonical distribution of
        harmonic oscillator. The unit of distance is Angstrom.
        shape=(number_of_snapshots, supercell_atoms, 3)
        dtype='double'

    """

    def __init__(self,
                 dynamical_matrix,
                 cutoff_frequency=None,
                 factor=VaspToTHz):
        """

        Parameters
        ----------
        supercell_matrix : array_like
            Supercell matrix.
            shape=(3, 3)
            dtype='intc'
        cutoff_frequency : float, optional
            Above this cutoff frequency in THz, it is decided if each phonon
            mode is included or not. Default is None but effectively 0.01 THz.
        factor : float, optional
            Unit conversion factor of phonon frequency to THz. Default is that
            for the VASP unit system.

        """

        self._dynmat = dynamical_matrix
        if cutoff_frequency is None or cutoff_frequency < 0:
            self._cutoff_frequency = 0.01
        else:
            self._cutoff_frequency = cutoff_frequency
        self._factor = factor
        self._T = None
        self.u = None

        self._unit_conversion = (Hbar * EV / AMU / THz
                                 / (2 * np.pi) / Angstrom ** 2)

        slat = self._dynmat.supercell.get_cell()
        self._rec_lat = np.linalg.inv(self._dynmat.primitive.get_cell())
        smat = np.rint(np.dot(slat, self._rec_lat).T).astype(int)
        self._comm_points = get_commensurate_points_in_integers(smat)
        self._ii, self._ij = self._categorize_points()
        assert len(self._ii) + len(self._ij) * 2 == len(self._comm_points)

        s2p = self._dynmat.primitive.s2p_map
        p2p = self._dynmat.primitive.p2p_map
        self._s2pp = [p2p[i] for i in s2p]

        self._eigvals_ii = []
        self._eigvecs_ii = []
        self._phase_ii = []
        self._eigvals_ij = []
        self._eigvecs_ij = []
        self._phase_ij = []
        self._prepare()

    def run(self, T, number_of_snapshots=1, seed=None):
        """

        Parameters
        ----------
        T : float
            Temperature in Kelvin.
        number_of_snapshots : int
            Number of snapshots to be generated.
        seed : int or None, optional
            Random seed passed to np.random.seed. Default is None. Integer
            number has to be positive.

        """

        np.random.seed(seed=seed)

        N = len(self._comm_points)
        u_ii = self._solve_ii(T, number_of_snapshots)
        u_ij = self._solve_ij(T, number_of_snapshots)
        mass = self._dynmat.supercell.get_masses().reshape(-1, 1)
        u = np.array((u_ii + u_ij) / np.sqrt(mass * N),
                     dtype='double', order='C')
        self.u = u

    def _prepare(self):
        pos = self._dynmat.supercell.get_scaled_positions()
        N = len(self._comm_points)

        qpoints_ii = get_qpoints_in_Brillouin_zone(
            self._rec_lat,
            self._comm_points[self._ii] / float(N),
            only_unique=True)
        for q in qpoints_ii:
            self._dynmat.set_dynamical_matrix(q)
            dm = self._dynmat.dynamical_matrix
            eigvals, eigvecs = np.linalg.eigh(dm.real)
            self._eigvals_ii.append(eigvals)
            self._eigvecs_ii.append(eigvecs)
            self._phase_ii.append(
                np.cos(2 * np.pi * np.dot(pos, q)).reshape(-1, 1))

        qpoints_ij = get_qpoints_in_Brillouin_zone(
            self._rec_lat,
            self._comm_points[self._ij] / float(N),
            only_unique=True)
        for q in qpoints_ij:
            self._dynmat.set_dynamical_matrix(q)
            dm = self._dynmat.dynamical_matrix
            eigvals, eigvecs = np.linalg.eigh(dm)
            self._eigvals_ij.append(eigvals)
            self._eigvecs_ij.append(eigvecs)
            self._phase_ij.append(
                np.exp(2j * np.pi * np.dot(pos, q)).reshape(-1, 1))

    def _solve_ii(self, T, number_of_snapshots):
        natom = self._dynmat.supercell.get_number_of_atoms()
        u = np.zeros((number_of_snapshots, natom, 3), dtype='double')

        for eigvals, eigvecs, phase in zip(
                self._eigvals_ii, self._eigvecs_ii, self._phase_ii):
            sigma = self._get_sigma(eigvals, T)
            dist_func = np.random.randn(
                number_of_snapshots, len(eigvals)) * sigma
            u_red = np.dot(dist_func, eigvecs.T).reshape(
                number_of_snapshots, -1, 3)[:, self._s2pp, :]
            u += u_red * phase

        return u

    def _solve_ij(self, T, number_of_snapshots):
        natom = self._dynmat.supercell.get_number_of_atoms()
        u = np.zeros((number_of_snapshots, natom, 3), dtype='double')

        for eigvals, eigvecs, phase in zip(
                self._eigvals_ij, self._eigvecs_ij, self._phase_ij):
            sigma = self._get_sigma(eigvals, T)
            dist_func = sigma * np.random.randn(
                2, number_of_snapshots, len(eigvals))
            u_red = np.dot(dist_func, eigvecs.T).reshape(
                2, number_of_snapshots, -1, 3)[:, :, self._s2pp, :]
            u += (u_red[0] * phase).real
            u -= (u_red[1] * phase).imag

        return u * np.sqrt(2)

    def _get_sigma(self, eigvals, T, mode=2):
        if mode == 0:  # Ignore modes having negative eigenvalues
            idx = np.where(eigvals * self._factor ** 2
                           > self._cutoff_frequency ** 2)[0]
            freqs = np.sqrt(eigvals[idx]) * self._factor
            n = 1.0 / (np.exp(freqs * THzToEv / (Kb * T)) - 1)
            sigma2 = self._unit_conversion / freqs * (0.5 + n)
            sigma = np.zeros(len(eigvals), dtype='double')
            sigma[idx] = np.sqrt(sigma2)
        elif mode == 1:  # Use absolute frequencies
            idx = np.where(abs(eigvals) * self._factor ** 2
                           > self._cutoff_frequency ** 2)[0]
            freqs = np.sqrt(abs(eigvals[idx])) * self._factor
            n = 1.0 / (np.exp(freqs * THzToEv / (Kb * T)) - 1)
            sigma2 = self._unit_conversion / freqs * (0.5 + n)
            sigma = np.zeros(len(eigvals), dtype='double')
            sigma[idx] = np.sqrt(sigma2)
        elif mode == 2:  # Raise to lowest positive absolute value
            idx = np.where(eigvals * self._factor ** 2
                           > self._cutoff_frequency ** 2)[0]
            idx_n = np.where(eigvals * self._factor ** 2
                             < -self._cutoff_frequency ** 2)[0]
            freqs = np.sqrt(eigvals[idx]) * self._factor
            n = 1.0 / (np.exp(freqs * THzToEv / (Kb * T)) - 1)
            sigma2 = self._unit_conversion / freqs * (0.5 + n)
            sigma = np.zeros(len(eigvals), dtype='double')
            sigma[idx] = np.sqrt(sigma2)
            sigma[idx_n] = sigma[idx[np.argmin(freqs)]]

        return sigma

    def _categorize_points(self):
        N = len(self._comm_points)
        ii = []
        ij = []
        for i, p in enumerate(self._comm_points):
            for j, _p in enumerate(self._comm_points):
                if ((p + _p) % N == 0).all():
                    if i == j:
                        ii.append(i)
                    elif i < j:
                        ij.append(i)
                    break
        return ii, ij
