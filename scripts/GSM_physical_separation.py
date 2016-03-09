__author__ = 'omniscope'

import numpy as np
import numpy.linalg as la
import sys
from matplotlib import cm
import healpy.pixelfunc as hpf
try:
    import healpy.visufunc as hpv
except:
    pass
import matplotlib.pyplot as plt

########################################
#load data
result_filename = '/mnt/data0/omniscope/polarized foregrounds/result_25+6_nside_128_smooth_8.73E-02_edge_8.73E-02_rmvcmb_1_UV0_v2.5_principal_6_step_1.00_err_none.npz'
f = np.load(result_filename)
w_nf = f['w_nf']#n_principal by frequency
x_ni = f['x_ni']#n_principal by pixel
freqs = f['freqs']#GHz
n_f = len(freqs)
n_principal = len(w_nf)
nside = hpf.npix2nside(x_ni.shape[1])
########################################
#embarassing fact: I have not been able to unify the units between sub-CMB, CMB, and above_CMB frequencies. If you guys know how to put those 3 into the same unit, it'll be super helpful.
normalization = f['normalization']

################################################
#plot orthogonal results
cmap = cm.gist_rainbow_r
cmap.set_under('w')
cmap.set_bad('gray')
def plot_components(M=np.eye(n_principal)):
    w_nf_local = M.dot(w_nf)
    x_ni_local = la.inv(M).transpose().dot(x_ni)
    for n in range(n_principal):


        sign_flip = np.sign(w_nf_local[n, np.argmax(np.abs(w_nf_local[n]))])

        plot_data_lin = x_ni_local[n] * sign_flip
        # if i == 0:
        #     plot_data = np.log10(plot_data)
        # else:
        plot_data = np.arcsinh(plot_data_lin * 1 / (np.median(np.abs(plot_data_lin))))
        try:
            hpv.mollview(plot_data, nest=True, sub=(2, n_principal, n + 1), cmap=cmap)
        except:
            print "NEED HEALPY PACKAGE FOR PLOTTING!"
        plt.subplot(2, n_principal, n_principal + n + 1)
        plt.plot(np.log10(freqs), sign_flip * w_nf_local[n])
        plt.plot(np.log10(freqs), sign_flip * w_nf_local[n], 'r+')
        plt.ylim([-1, 1])
    plt.show()

eigen_values = np.zeros((n_f, n_f, n_principal))
eigen_vecs = np.zeros((n_f, n_f, n_principal, n_principal))
ex_eigen_values = np.zeros((n_f, n_f, n_principal))#eigenvalues for sections excluding f0-f1
ex_eigen_vecs = np.zeros((n_f, n_f, n_principal, n_principal))#eigenvecs for sections excluding f0-f1
for fstart in range(n_f):
    for fend in range(n_f):
        f_range_mask = np.zeros(n_f, dtype='bool')
        f_range_mask[fstart:fend+1] = True
        tmp_w_nf = w_nf[:, f_range_mask]
        eigen_values[fstart, fend], eigen_vecs[fstart, fend] = la.eigh(tmp_w_nf.dot(np.transpose(tmp_w_nf)))
        tmp_w_nf = w_nf[:, ~f_range_mask]
        ex_eigen_values[fstart, fend], ex_eigen_vecs[fstart, fend] = la.eigh(tmp_w_nf.dot(np.transpose(tmp_w_nf)))

plot_components()
###STEP 1
###excluding range eigenvalue approach: pick out clean modes
w_nf_intermediate = np.copy(w_nf)
fs_intermediate = np.arange(n_f)
M = np.eye(n_principal)
project_M = np.eye(n_principal)
max_component_range = 12
max_thresh = 1e-2
start_n_principal = 0
for i in range(n_principal):
    project_range = [0, max_component_range]
    thresh = 1e-4
    project_M = np.eye(n_principal - i)
    while np.allclose(project_M, np.eye(n_principal - i)) and thresh <= max_thresh:

        for f0, fstart in enumerate(fs_intermediate):
            for f1, fend in enumerate(fs_intermediate):
                if f1-f0 < project_range[1] - project_range[0]:
                    f_range_mask = np.ones(len(fs_intermediate), dtype='bool')
                    f_range_mask[f0:f1+1] = False
                    tmp_w_nf = w_nf_intermediate[:, f_range_mask]
                    ev, ec = la.eigh(tmp_w_nf.dot(np.transpose(tmp_w_nf)))
                    if ev[0] / np.sum(ev) <= thresh:
                        project_range = [f0, f1]
                        project_M = ec.transpose()
        thresh *= 2
    if thresh > max_thresh:
        break
    print thresh, fs_intermediate[project_range]
    project_M = project_M / la.norm(project_M.dot(w_nf_intermediate), axis=-1)[:, None]

    # new_w_nf = project_M.dot(w_nf_intermediate)
    # isolated_mode = new_w_nf[0]
    # isolate_matrix = np.eye(n_principal - i)
    # isolate_matrix[1:, 0] = -new_w_nf[1:].dot(isolated_mode) / isolated_mode.dot(isolated_mode)
    # project_M = isolate_matrix.dot(project_M)

    M[i:] = project_M.dot(M[i:])
    w_nf_intermediate = project_M.dot(w_nf_intermediate)[1:]
    start_n_principal += 1
    # fs_intermediate = np.concatenate((fs_intermediate[:project_range[0]], fs_intermediate[project_range[1]+1:]))
plot_components(M)

###normal inclusive eigenvalue approach: not very effective
# eigenvalue_fractions = eigen_values / np.sum(eigen_values, axis=-1)[..., None]
# eigenvalue_cumu_fractions = np.cumsum(eigenvalue_fractions, axis=-1)
#
# for i in range(n_principal-1):
#     plt.subplot(2, n_principal - 1, i+1)
#     plt.imshow(np.log10(eigen_values[..., -i-1] / eigen_values[..., -i-2]), interpolation='none', vmax=3, vmin=1)
#     plt.title('ratio of %ist and %ist eigen'%(i+1, i+2))
#
#
# #calculate how many eigen modes it takes to explain 99%
# for j, thresh in enumerate([5e-2, 1e-2, 1e-3, 1e-4]):
#     n_modes = np.zeros((n_f, n_f), dtype='int')
#
#     for fstart in range(n_f):
#         for fend in range(n_f):
#             for i in range(n_principal - 1):
#                 if eigenvalue_cumu_fractions[fstart, fend, i] >= thresh:
#                     n_modes[fstart, fend] = n_principal - i
#                     break
#                 elif eigenvalue_cumu_fractions[fstart, fend, i] < thresh and eigenvalue_cumu_fractions[fstart, fend, i + 1] >= thresh:
#                     n_modes[fstart, fend] = n_principal - 1 - i
#                     break
#     plt.subplot(2, n_principal - 1, j+n_principal)
#     plt.imshow(n_modes, interpolation='none')
#     plt.title('number of modes to exclude only %.1e'%thresh)
# plt.show()
#
###STEP 2
###dominating eigenvalue approach: not very effective
# M = np.eye(n_principal)
# start_n_principal = 2
w_nf_intermediate = M.dot(w_nf)[start_n_principal:]
fs_intermediate = np.arange(n_f)
# project_M = np.eye(n_principal)
for i in range(n_principal - start_n_principal):

    thresh = 1e-3
    project_range = [-1, -1]
    for f0, fstart in enumerate(fs_intermediate):
        for f1, fend in enumerate(fs_intermediate):
            if f1-f0 > project_range[1] - project_range[0]:
                tmp_w_nf = w_nf_intermediate[:, f0:f1+1]
                ev, ec = la.eigh(tmp_w_nf.dot(np.transpose(tmp_w_nf)))
                if ev[-1] / np.sum(ev) >= 1 - thresh:
                    project_range = [f0, f1]
                    project_M = ec.transpose()
    print fs_intermediate[project_range]
    project_M = project_M / la.norm(project_M.dot(w_nf_intermediate), axis=-1)[:, None]
    M[start_n_principal:n_principal-i] = project_M.dot(M[start_n_principal:n_principal-i])
    w_nf_intermediate = project_M.dot(w_nf_intermediate)[:n_principal-start_n_principal-i-1]
    fs_intermediate = np.concatenate((fs_intermediate[:project_range[0]], fs_intermediate[project_range[1]+1:]))
plot_components(M)


# #STEP 3: cmb
# x_ni_intermediate = np.transpose(la.inv(M)).dot(x_ni)
# cmb_m = np.eye(n_principal)#remove foreground in CMB
# # plane_mask = np.abs(hpf.pix2ang(nside, np.arange(12*nside**2), nest=True)[0] - np.pi / 2) < np.pi/36
# if abs(np.min(x_ni_intermediate[0])) > np.max(x_ni_intermediate[0]):
#     plane_mask = x_ni_intermediate[0] <  -np.max(x_ni_intermediate[0])
# else:
#     plane_mask = x_ni_intermediate[0] >  abs(np.min(x_ni_intermediate[0]))
# cmb_m[0, 1:] = -la.inv(x_ni_intermediate[1:, plane_mask].dot(np.transpose(x_ni_intermediate[1:, plane_mask]))).dot(x_ni_intermediate[1:, plane_mask].dot(x_ni_intermediate[0, plane_mask]))
#
#
# x_ni_intermediate = cmb_m.dot(x_ni_intermediate)
# cmb_m2 = np.eye(n_principal)#remove cmb from foregrounds
# # for i in range(1, n_principal):
# #     cmb_m2[i, 0] = -x_ni_intermediate[0, ~plane_mask].dot(x_ni_intermediate[i, ~plane_mask]) / x_ni_intermediate[0, ~plane_mask].dot(x_ni_intermediate[0, ~plane_mask])
#
# M = la.inv(cmb_m2.dot(cmb_m).transpose()).dot(M)
# M = M / la.norm(M.dot(w_nf), axis=-1)[:, None]
# plot_components(M)
sys.exit(0)
######################################################
##quick example of using eigen values in w_nf to search for modes that are limited in frequency range
##as I shrink the range of frequencies, the number of non-zero eigen values decreases
eigen_values = np.zeros((n_f, n_principal))
for f_end in range(n_f):
    tmp_w_nf = w_nf[:, :f_end+1]
    eigen_values[f_end], evector = la.eigh(tmp_w_nf.dot(np.transpose(tmp_w_nf)))
plt.subplot(1, 2, 1)
plt.imshow(eigen_values, interpolation='none')

eigen_values = np.zeros((n_f, n_principal))
for f_start in range(n_f):
    tmp_w_nf = w_nf[:, f_start:]
    eigen_values[f_start], evector = la.eigh(tmp_w_nf.dot(np.transpose(tmp_w_nf)))
plt.subplot(1, 2, 2)
plt.imshow(eigen_values, interpolation='none')
plt.show()