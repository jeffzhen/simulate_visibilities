import simulate_visibilities.Bulm as Bulm
import simulate_visibilities.simulate_visibilities as sv
import numpy as np
import numpy.linalg as la
import scipy.linalg as sla
import time, ephem, sys, os, resource, datetime, warnings
import aipy as ap
import matplotlib.pyplot as plt
import healpy as hp
import healpy.rotator as hpr
import healpy.pixelfunc as hpf
import healpy.visufunc as hpv
import scipy.interpolate as si
import glob
import fitsio
import omnical.calibration_omni as omni

def fit_power(freq, amp):
    b = np.log10(amp)
    A = np.ones((len(freq), 2))
    A[:, 0] = np.log10(freq)
    AtAi = la.inv(A.transpose().dot(A))
    x = AtAi.dot(A.transpose().dot(b))
    error = A.dot(x) - b
    noise = la.norm(error) / (len(freq) - 2)**.5
    return x[0], AtAi[0, 0]**.5 * noise
def add_diag_in_place(M, diag):
    if M.shape[0] != M.shape[1] or M.shape[0] != len(diag):
        raise ValueError('Shape Mismatch: %s and %i'%(M.shape, len(diag)))
    M.shape = (len(diag) ** 2)
    M[::len(diag) + 1] += diag
    M.shape = (len(diag), len(diag))
    return

PI = np.pi
TPI = PI * 2

nside_standard = 256
standard_freq = 150.
nside = 64
total_valid_npix = 12*nside**2
pixel_dir = '/home/omniscope/data/GSM_data/absolute_calibrated_data/'
selfcal = True
empirical_noise = False


instrument= 'miteor'#, 'miteor_compact']#'paper']#, 'miteor']
valid_npixs = {'miteor': 41832}#{'paper': 14896, 'miteor': 10428, 'miteor_compact': 12997}
datatags = {'paper': '_lstbineven_avg4', 'miteor': '_2016_01_20_avg2_unpollock', 'miteor_compact': '_2016_01_20_avg'}
vartags = {'paper': '_lstbineven_avg4', 'miteor': '_2016_01_20_avg2_unpollock', 'miteor_compact': '_2016_01_20_avgx100'}
datadirs = {'paper': '/home/omniscope/data/PAPER/lstbin_fg/even/', 'miteor': '/home/omniscope/data/GSM_data/absolute_calibrated_data/', 'miteor_compact': '/home/omniscope/data/GSM_data/absolute_calibrated_data/'}
bnsides = {'paper': 64, 'miteor': 256, 'miteor_compact': 256}
# noise_scales = {'paper': 10., 'miteor': 1., 'miteor_compact': 1.}

relative_noise_scales = {}
absolute_noise_scale = 1
if selfcal:
    file_tag = '_selfcal'
elif empirical_noise:
    chi2_file = np.load(datadirs[instrument]+instrument+'_chi2.npz')
    absolute_noise_scale = np.mean(chi2_file['chi2s']**.5)
    for i, Q in enumerate(chi2_file['Qs']):
        relative_noise_scales[Q] = chi2_file['chi2s'][i]**.5 / absolute_noise_scale
    print absolute_noise_scale
    print relative_noise_scales
    file_tag = '_empirical_noise'
else:
    file_tag = ''
Qs = []
A_fns = []
AtNiA_fns = []
data_fns = []
datatag = datatags[instrument]
vartag = vartags[instrument]
datadir = datadirs[instrument]
valid_npix = valid_npixs[instrument]
instru_data_fns = glob.glob(datadir + '*' + datatag + vartag + '_gsmcal_n%i_bn%i.npz'%(nside_standard, bnsides[instrument]))
data_fns += instru_data_fns
for data_fn in instru_data_fns:
    Q = os.path.basename(data_fn).split(datatag)[0]
    Qs.append(Q)

    A_candidates = glob.glob(datadir + Q + 'A_dI*p%i*'%valid_npix)
    if len(A_candidates) != 1:
        raise IOError("Not unique files for %s: %s. Searched %s."%(Q, A_candidates, datadir + Q + 'A_dI*p%i*'%valid_npix))
    else:
        A_fn = A_candidates[0]

    AtNiA_candidates = glob.glob(datadir + Q + 'AtNiA_N%s_noadd*%s'%(vartag, A_fn.split(Q)[1]))
    if len(AtNiA_candidates) != 1:
        raise IOError("Not unique files for %s: %s. Searched %s."%(Q, AtNiA_candidates, datadir + Q + 'AtNiA_N%s_noadd%s'%(vartag, A_fn.split(Q)[1])))
    else:
        AtNiA_fn = AtNiA_candidates[0]
    A_fns.append(A_fn)
    AtNiA_fns.append(AtNiA_fn)

###get metadata
tlists = {}
ubls = {}
datas = {}
Nis = {}
freqs = np.zeros(len(data_fns))
for i, (data_fn, Q) in enumerate(zip(data_fns, Qs)):
    data_file = np.load(data_fn)
    freqs[i] = data_file['freq']
    ubls[Q] = data_file['ubls']
    tlists[Q] = data_file['tlist']
    datas[Q] = data_file['data']
    Nis[Q] = data_file['Ni']
print freqs

###pixel scheme
pixel_scheme_file = np.load(pixel_dir + 'pixel_scheme_%i.npz'%total_valid_npix)
fake_solution_map = pixel_scheme_file['gsm']
thetas = pixel_scheme_file['thetas']
phis= pixel_scheme_file['phis']
sizes= pixel_scheme_file['sizes']
nside_distribution= pixel_scheme_file['nside_distribution']
final_index= pixel_scheme_file['final_index']
npix = pixel_scheme_file['n_fullsky_pix']
valid_pix_mask= pixel_scheme_file['valid_pix_mask']
thresh= pixel_scheme_file['thresh']

sub_pixel_files = {}
sub_pixel_files[instrument] = np.load(pixel_dir + 'pixel_scheme_%i.npz'%valid_npixs[instrument])
child_mask = sub_pixel_files[instrument]['child_mask']



##################
###start calculations
###############
n_iter = 0
max_iter = 15
npix_for_A_on_disk = np.sum(child_mask)
non_zero_mask = np.ones(npix_for_A_on_disk, dtype=bool)#will change after one iteration
npix_nonzero = np.sum(non_zero_mask)
max_angle = 1.
max_Q = (0,0)
errors = {}
chi2s = {}
fits = {}
amp_fits = {}

def check_existense(n_iter, npix_nonzero):
    AtNiA_filename = datadirs['miteor'] + 'mega_AtNiA_n%i_iter%i'%(npix_nonzero, n_iter) + file_tag
    AtNid_filename = datadirs['miteor'] + 'mega_AtNid_n%i_iter%i'%(npix_nonzero, n_iter) + file_tag
    AtNisimd_filename = datadirs['miteor'] + 'mega_AtNisimd_n%i_iter%i'%(npix_nonzero, n_iter) + file_tag
    return os.path.isfile(AtNiA_filename) and os.path.isfile(AtNid_filename) and (os.path.isfile(AtNisimd_filename) or n_iter != max_iter - 1)


###re-weighting iteration
while n_iter < max_iter and max_angle > 1. / nside / 10.:
    print '================================================='
    print '================ITER #%i=========================='%n_iter
    sys.stdout.flush()
    if n_iter != max_iter - 1 and check_existense(n_iter + 1, npix_nonzero):
        continue

    AtNiA_filename = datadirs['miteor'] + 'mega_AtNiA_n%i_iter%i'%(npix_nonzero, n_iter) + file_tag
    AtNid_filename = datadirs['miteor'] + 'mega_AtNid_n%i_iter%i'%(npix_nonzero, n_iter) + file_tag
    d_filename = datadirs['miteor'] + 'mega_d_n%i_iter%i.npz'%(npix_nonzero, n_iter) + file_tag + '.npz'
    AtNisimd_filename = datadirs['miteor'] + 'mega_AtNisimd_n%i_iter%i'%(npix_nonzero, n_iter) + file_tag
    weight_filename = datadirs['miteor'] + 'mega_weight_n%i_iter%i'%(npix_nonzero, n_iter) + file_tag
    if os.path.isfile(AtNiA_filename) and os.path.isfile(AtNid_filename) and (os.path.isfile(AtNisimd_filename) or n_iter != max_iter - 1):
        AtNiA_sum = np.fromfile(AtNiA_filename, dtype='float64')
        AtNiA_sum.shape = (npix_nonzero, npix_nonzero)
        AtNidata_sum = np.fromfile(AtNid_filename, dtype='float64')
        if n_iter == max_iter - 1:
            AtNisimdata_sum = np.fromfile(AtNisimd_filename, dtype='float64')
        if n_iter == 0:
            weights = np.array([(freq / standard_freq)**-2.5 for freq in freqs])
        else:
            weights = np.fromfile(weight_filename, dtype='float64')
            d_file = np.load(d_filename)
            for Q in d_file.keys():
                datas[Q] = d_file[Q]


    else:
        AtNidata_sum = np.zeros(npix_nonzero, dtype='float64')
        # AtNiptdata_sum = np.zeros((2, npix_nonzero), dtype='float64')

        if n_iter == max_iter - 1:
            AtNisimdata_sum = np.zeros(npix_nonzero, dtype='float64')
        AtNiA_sum = np.zeros((npix_nonzero, npix_nonzero), dtype='float64')
        weights = np.zeros(len(data_fns), dtype='float64')#synchrotron scaling, [divide data by weight and mult Ni and AtNiA by weight**2], or [multiply A by weight and AtNiA by weight**2]

        if selfcal:
            if n_iter == 0:
                absolute_noise_scale = 1.
                for Q in Qs:
                    relative_noise_scales[Q] = 1.

        max_Q = 'none'
        max_angle = 0
        for i, (Q, data_fn, A_fn, AtNiA_fn) in enumerate(zip(Qs, data_fns, A_fns, AtNiA_fns)):
            print Q, np.sum(child_mask), len(child_mask)
            sys.stdout.flush()

            if not selfcal and not empirical_noise:
                relative_noise_scales[Q] = 1.

            data_file = np.load(data_fn)
            data = datas[Q]
            nUBL = len(ubls[Q])
            nt = len(tlists[Q])
            Ni = Nis[Q]

            A = (np.fromfile(A_fn, dtype='float32').reshape((len(data)/2, valid_npix + 4*nUBL, 2))[:, :valid_npix][:, non_zero_mask]).transpose((2, 0, 1)).reshape((len(data), npix_nonzero))
            if n_iter == 0:
                weights[i] = (freqs[i] / standard_freq)**-2.5
            else:#selfcal

                Ax = A.dot(result)
                #get amplitude scaling
                def reshape_data(real_data):
                    if len(real_data.flatten()) != 2 * nUBL * 2 * nt:
                        raise ValueError("Incorrect dimensions: data has length %i where nubl %i and nt %i together require length of %i."%(len(real_data), nUBL, nt, 2 * nUBL * 2 * nt))
                    input_shape = real_data.shape
                    real_data.shape = (2 * nUBL * 2, nt)
                    result = np.copy(real_data).transpose()
                    real_data.shape = input_shape
                    return result

                def get_complex_data(real_data, chi2=False):
                    if len(real_data.flatten()) != 2 * nUBL * 2 * nt:
                        raise ValueError("Incorrect dimensions: data has length %i where nubl %i and nt %i together require length of %i."%(len(real_data), nUBL, nt, 2 * nUBL * 2 * nt))
                    input_shape = real_data.shape
                    real_data.shape = (2, nUBL, 2, nt)
                    if chi2:
                        result = np.copy(real_data)
                    else:
                        result = real_data[0] + 1.j * real_data[1]
                    real_data.shape = input_shape
                    return result


                data = get_complex_data(data)
                for p in range(2):
                    psol = omni.solve_phase_degen_fast(data[:, p].transpose(), data[:, p].transpose(), get_complex_data(Ax)[:, p].transpose(), get_complex_data(Ax)[:, p].transpose(), ubls[Q])
                    print psol,
                    if np.max(ubls[Q][:, :2].dot(psol)) > max_angle:
                        max_angle = np.max(ubls[Q][:, :2].dot(psol))
                        max_Q = (Q, p)
                    data[:, p] *= np.exp(1.j * ubls[Q][:, :2].dot(psol))[:, None]
                data = np.concatenate((np.real(data).flatten(), np.imag(data).flatten())).flatten()

                ampcals = np.array([np.sum((data * Ni * Ax).reshape((2, nUBL, 2, nt))[:, :, p]) / np.sum((Ax * Ni * Ax).reshape((2, nUBL, 2, nt))[:, :, p]) for p in range(2)])
                data.shape = (2, nUBL, 2, nt)
                #NOTE: should rescale Ni as well, but AtNiA is read from file on disk and cant rescale Ni separately for xx and yy, so not rescaling Ni, meaning xx and yy can have relatively wrong Ni level by up to 5%
                # Ni.shape = (2, nUBL, 2, nt)
                data /= (ampcals[:, None] / np.mean(ampcals))
                # Ni *= (ampcals[:, None] / np.mean(ampcals))**2
                data = data.flatten()
                # Ni = Ni.flatten()
                datas[Q] = data
                # Nis[Q] = Ni
                print ampcals

                weights[i] = np.sum(data * Ni * Ax) / np.sum(Ax * Ni * Ax)
                fit = Ax * weights[i]
                amp_fit = np.array([d.dot(ni * ft) / ft.dot(ni * ft) for d, ft, ni in zip(reshape_data(data), reshape_data(fit), reshape_data(Ni))]) * weights[i]

                error = fit - data
                chi2 = error**2 * Ni
                errors[Q] = get_complex_data(error)

                fits[Q] = get_complex_data(fit)
                amp_fits[Q] = amp_fit

                if selfcal:#since we are calculating per Q error on the fly, we leave  absolute_noise_scale to 1 until we have finished through all Qs and correct this overall factor later
                    absolute_noise_scale = 1.
                    relative_noise_scales[Q] = np.mean(chi2)**.5
                    chi2 /= relative_noise_scales[Q]**2

                chi2s[Q] = get_complex_data(chi2, chi2=True)
            # nUBL = int(A_fn.split(Q + 'A_dI_u')[1].split('_')[0])
            At = A.transpose()#(np.fromfile(A_fn, dtype='float32').reshape((len(data)/2, valid_npix + 4*nUBL, 2))[:, :valid_npix][:, non_zero_mask]).transpose((1, 2, 0)).reshape((npix_nonzero, len(data)))

            AtNidata_sum += At.dot(data * Ni) * weights[i] / relative_noise_scales[Q]**2
            AtNiA_sum += np.fromfile(AtNiA_fn, dtype='float64').reshape((valid_npix, valid_npix))[np.ix_(non_zero_mask, non_zero_mask)] * weights[i]**2 / relative_noise_scales[Q]**2

            if n_iter == max_iter - 1:
                AtNisimdata_sum += At.dot(data_file['simdata'] * Ni) * weights[i] / relative_noise_scales[Q]**2
        if selfcal and n_iter != 0:
            absolute_noise_scale = np.mean(np.array([relative_noise_scales[Q] for Q in Qs]))
            AtNiA_sum *= absolute_noise_scale **2
            AtNidata_sum *= absolute_noise_scale **2
        AtNiA_sum.tofile(AtNiA_filename)
        AtNidata_sum.tofile(AtNid_filename)
        np.savez(d_filename, **datas)
        if n_iter == max_iter - 1:
            if selfcal:
                AtNisimdata_sum *= absolute_noise_scale **2
            AtNisimdata_sum.tofile(AtNisimd_filename)
        weights.tofile(weight_filename)
    if n_iter == 0:
        non_zero_mask = (np.diagonal(AtNiA_sum) != 0)
        npix_nonzero = np.sum(non_zero_mask)
        AtNiA_sum = AtNiA_sum[np.ix_(non_zero_mask, non_zero_mask)]
        AtNidata_sum = AtNidata_sum[non_zero_mask]
        if n_iter == max_iter - 1:
            AtNisimdata_sum = AtNisimdata_sum[non_zero_mask]
    if n_iter != 0:
        plt.plot(sorted(freqs), weights[np.argsort(freqs)])

    for reg in 10.**np.arange(-6, -5, .5):
        AtNiAi_filename = datadirs['miteor'] + 'mega_AtNiAi_n%i_iter%i_reg%.3e'%(npix_nonzero, n_iter, reg) + file_tag
        if os.path.isfile(AtNiAi_filename):
            AtNiAi = sv.InverseCholeskyMatrix.fromfile(AtNiAi_filename, npix_nonzero, 'float64')
            break
        else:
            print "trying", reg, datetime.datetime.now()
            sys.stdout.flush()
            timer = time.time()
            try:
                add_diag_in_place(AtNiA_sum, np.ones(npix_nonzero) * reg)
                AtNiAi = sv.InverseCholeskyMatrix(AtNiA_sum)
                print "%f minutes used" % (float(time.time() - timer) / 60.)
                sys.stdout.flush()
                AtNiAi.tofile(AtNiAi_filename)
                del AtNiA_sum
                break
            except TypeError:
                continue

    result = AtNiAi.dotv(AtNidata_sum)
    n_iter += 1
    print max_Q, max_angle, absolute_noise_scale
if max_iter != 1:
    plt.show()


#####################################
####error analysis and spectral index change
#####################################

print "###Error analysis####"
sys.stdout.flush()
for i, (Q, data_fn, A_fn, AtNiA_fn) in enumerate(zip(Qs, data_fns, A_fns, AtNiA_fns)):
    ubl_len = la.norm(ubls[Q], axis=-1)
    ubl_sort = np.argsort(ubl_len)
    plt.subplot(2, 2, 1)
    plt.plot(sorted(ubl_len), la.norm(la.norm(errors[Q], axis=-1), axis=-1)[ubl_sort], label=Q)
    plt.subplot(2, 2, 2)
    plt.plot(sorted(ubl_len), [np.mean(chi2s[Q][:, u]) for u in ubl_sort], label=Q)
    plt.subplot(2, 2, 3)
    plt.plot((tlists[Q] - 5)%24 + 5, [la.norm(errors[Q][..., t]) for t in range(errors[Q].shape[-1])], label=Q)
    plt.subplot(2, 2, 4)
    plt.plot((tlists[Q] - 5)%24 + 5, [np.mean(chi2s[Q][..., t]) for t in range(chi2s[Q].shape[-1])], label=Q)
plt.legend()
plt.show()

for q, Q in enumerate(sorted(Qs)):
    plt.subplot(4, 5, q+1)
    nUBL = len(ubls[Q])
    nt = len(tlists[Q])
    def get_complex_data(real_data, chi2=False):
        if len(real_data.flatten()) != 2 * nUBL * 2 * nt:
            raise ValueError("Incorrect dimensions: data has length %i where nubl %i and nt %i together require length of %i."%(len(real_data), nUBL, nt, 2 * nUBL * 2 * nt))
        input_shape = real_data.shape
        real_data.shape = (2, nUBL, 2, nt)
        if chi2:
            result = np.copy(real_data)
        else:
            result = real_data[0] + 1.j * real_data[1]
        real_data.shape = input_shape
        return result
    fun=np.angle
    for u in range(nUBL):
        plt.plot((tlists[Q] -3)%24 + 3, (fun(fits[Q][u, 0]) - fun(get_complex_data(datas[Q])[u, 0]) + PI)%TPI - PI)
    plt.xlim([17, 26])
    plt.ylim([-3.2, 3.2])
    plt.title(Q + ' %.2e'%relative_noise_scales[Q])
plt.show()

if not empirical_noise and not selfcal:
    np.savez(datadir+instrument+'_chi2.npz', Qs=Qs, chi2s=np.array([np.mean(chi2s[Q]) for i, Q in enumerate(Qs)]))

###grid amp_fit into a dictionary
amp_tf_grid = {}
t_grid_size = .5
for i, Q in enumerate(Qs):
    t = t_grid_size / 2.
    while t < 24.:
        insert_fit = amp_fits[Q][np.abs(tlists[Q] - t) <= t_grid_size / 2.]
        if len(insert_fit) > 0:
            amp_tf_grid[(t, freqs[i])] = np.nanmean(insert_fit)
        t += t_grid_size

###grid dictionary  into arrays
keys_array = np.array(amp_tf_grid.keys())
spectral_index_list = []
t = t_grid_size / 2.
plt.subplot(1, 2, 1)
while t < 24.:
    mask = keys_array[:, 0] == t
    if mask.any():
        sort_mask = np.argsort(keys_array[mask, 1])
        tmp_freqs = keys_array[mask, 1][sort_mask]
        tmp_amps = [amp_tf_grid[tuple(key)] for key in keys_array[mask][sort_mask]]
        plt.plot(tmp_freqs, tmp_amps, label=t)
        spectral, spectral_error = fit_power(tmp_freqs[3:] * 1e6, tmp_amps[3:])
        spectral_index_list.append([t, spectral, spectral_error])
    t += t_grid_size
plt.legend()
plt.xlabel('Freq (MHz)')
plt.ylabel('Amplitude ratio')

plt.subplot(1, 2, 2)
spectral_index_list = np.array(spectral_index_list)
plt.errorbar((spectral_index_list[:, 0] - 5)%24 + 5, spectral_index_list[:, 1], fmt='g+', yerr=spectral_index_list[:, 2])
plt.xlabel('LST (hour)')
plt.ylabel('Spectral index')
plt.ylim(-4, -1)
plt.show()
print np.sum(spectral_index_list[:, 1] / spectral_index_list[:,2]**2) / np.sum(1 / spectral_index_list[:,2]**2), np.sum(1 / spectral_index_list[:,2]**2)**-.5

##############################################
###make pretty maps with heavy regularization
AtNiA_sum = np.fromfile(AtNiA_filename, dtype='float64')
AtNiA_sum.shape = (npix_nonzero, npix_nonzero)
for reg in 10.**np.arange(-4.5, -2, .5):
    AtNiAi_filename = datadirs['miteor'] + 'mega_AtNiAi_n%i_iter%i_reg%.3e'%(npix_nonzero, max_iter - 1, reg) + file_tag
    if os.path.isfile(AtNiAi_filename):
        AtNiAi = sv.InverseCholeskyMatrix.fromfile(AtNiAi_filename, npix_nonzero, 'float64')
        break
    else:
        print "trying", reg, datetime.datetime.now()
        sys.stdout.flush()
        timer = time.time()
        try:
            add_diag_in_place(AtNiA_sum, np.ones(npix_nonzero) * reg)
            AtNiAi = sv.InverseCholeskyMatrix(AtNiA_sum)
            print "%f minutes used" % (float(time.time() - timer) / 60.)
            sys.stdout.flush()
            AtNiAi.tofile(AtNiAi_filename)
            del AtNiA_sum
            # AtNiAi0 = sv.InverseCholeskyMatrix(AtNiA_sum0 + np.eye(total_valid_npix) * reg)
            break
        except TypeError:
            continue



result = AtNiAi.dotv(AtNidata_sum)
# result0 = AtNiAi0.dotv(AtNidata_sum0)
# pt_results = np.array([AtNiAi.dotv(AtNiptdata) for AtNiptdata in AtNiptdata_sum])
sim_result = AtNiAi.dotv(AtNisimdata_sum)
# sim_result0 = AtNiAi0.dotv(AtNisimdata_sum0)
########################
#####plot stuff in mollwide
#########################
total_mask = np.copy(child_mask)
total_mask[total_mask] = non_zero_mask
def sol2map(sol, std=False):
    full_sol = np.zeros(npix)

    full_sol[total_mask] = sol
    if std:
        full_sol /= (sizes)**.5
    else:
        full_sol /= sizes
    return full_sol

def plot_IQU(solution, title, col, shape=(1, 1), coord='C', std=False, log=True, min=0, max=4, nside_out=None):
    # Es=solution[np.array(final_index).tolist()].reshape((4, len(final_index)/4))
    # I = Es[0] + Es[3]
    # Q = Es[0] - Es[3]
    # U = Es[1] + Es[2]
    I = sol2map(solution, std=std)
    if nside_out != None:
        I = hpf.ud_grade(I, nside_out=nside_out, order_in='NESTED', order_out='NESTED')
    plotcoordtmp = coord
    if log:
        hpv.mollview(np.log10(I), min=min, max=max, coord=plotcoordtmp, title=title, nest=True, sub=(shape[0], shape[1], col))
    else:
        hpv.mollview(I, min=min, max=max, coord=plotcoordtmp, title=title, nest=True, sub=(shape[0], shape[1], col))
    if col == shape[0] * shape[1]:
        plt.show()


rescale = (nside_standard / nside)**2
plot_IQU(result / rescale, instrument, 1, shape=(2, 2), coord='CG')
plot_IQU(sim_result / rescale, 'noiseless simulation', 2, shape=(2, 2), coord='CG')
# plot_IQU(result0 / rescale, '+'.join(INSTRUMENTS) + ' 0iter', 10, shape=(3, 4), coord='CG')
# plot_IQU(sim_result0 / rescale, 'noiseless simulation 0iter', 11, shape=(3, 4), coord='CG')
# for i, pt_result in enumerate(pt_results):
#     plot_IQU(pt_result / rescale, data_file['pt_sources'][i], 6 + 2*i, shape=(3, 4), coord='CG')
#     plot_IQU(np.abs(pt_result) / rescale, 'abs '+data_file['pt_sources'][i], 7 + 2*i, shape=(3, 4), coord='CG')

# #####GSM reg version
# I_supress = 25.
# S_diag = (fake_solution_map * rescale)** 2 / I_supress
# #add S inverse to AtNiA in a messy way to save memory usage
# AtNiASi = np.copy(AtNiA_sum)
# AtNiASi.shape = (len(AtNiASi) ** 2)
# AtNiASi[::len(S_diag) + 1] += 1./S_diag
# AtNiASi.shape = (len(S_diag), len(S_diag))
# AtNiASii = sv.InverseCholeskyMatrix(AtNiASi)
# AtNiSidata = AtNidata_sum + fake_solution_map * rescale / S_diag
# combined_result = AtNiASii.dotv(AtNiSidata)
#
# plot_IQU(combined_result / rescale, '+'.join(INSTRUMENTS) + '+GSM', 2, shape=(3, 4), coord='CG')
#


###parkes
parkes_header = fitsio.read_header("/home/omniscope/data/polarized foregrounds/parkes_150mhz.bin")
parkes_150 = fitsio.read("/home/omniscope/data/polarized foregrounds/parkes_150mhz.bin")[0]
parkes_150[:, :-1] = np.roll(parkes_150[:, :-1], 180, axis=1)[:, ::-1]
parkes_150[:, -1] = parkes_150[:, 0]
parkes_150[parkes_150 > parkes_header['DATAMAX']] = -parkes_header['DATAMAX']
parkes_150[parkes_150 < parkes_header['DATAMIN']] = -parkes_header['DATAMAX']
parkes_150 = sv.equirectangular2heapix(parkes_150, nside, nest=False)
parkes_150[parkes_150 <= 0] = np.nan
equ2013_to_gal_matrix = hp.rotator.Rotator(coord='cg').mat.dot(sv.epoch_transmatrix(2000, stdtime=2013.58))
ang0, ang1 = hp.rotator.rotateDirection(equ2013_to_gal_matrix, hpf.pix2ang(nside, range(12 * nside ** 2), nest=True))
parkes_150 = hpf.get_interp_val(parkes_150, ang0, ang1)

hpv.mollview(np.log10(parkes_150), nest=True, min=0, max=4, sub=(2, 2, 3), title='parkes150MHz', coord='CG')


def smoothing(m, fwhm, nest=True):
    full_map = np.ones(npix)
    full_map[total_mask] = m
    if fwhm <= 0:
        return m
    if nest:
        smoothed_map = hpf.reorder(hp.smoothing(hpf.reorder(full_map, n2r=True), fwhm=fwhm), r2n=True)
    else:
        smoothed_map = hp.smoothing(full_map, fwhm=fwhm)
    return smoothed_map[total_mask]

####GSM####
gsm = smoothing(fake_solution_map[total_mask], ((PI/90.)**2 - (PI/200)**2)**.5)
plot_IQU(gsm, 'GSM', 4, shape=(2, 2), coord='CG')
plt.show()


bright_points = {'cyg':{'ra': '19:59:28.3', 'dec': '40:44:02'}, 'cas':{'ra': '23:23:26', 'dec': '58:48:00'}}
pt_source_range = PI / 60
smooth_scale = PI / 30
pt_source_neighborhood_range = [smooth_scale, PI / 9]
bright_pt_mask = np.zeros(npix_nonzero, dtype=bool)
bright_pt_neighborhood_mask = np.zeros(npix_nonzero, dtype=bool)
for source in bright_points.keys():
    bright_points[source]['body'] = ephem.FixedBody()
    bright_points[source]['body']._ra = bright_points[source]['ra']
    bright_points[source]['body']._dec = bright_points[source]['dec']
    theta = PI / 2 - bright_points[source]['body']._dec
    phi = bright_points[source]['body']._ra
    pt_coord = np.array([np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta)])
    sky_vecs = np.array(hpf.pix2vec(nside, np.arange(hpf.nside2npix(nside)), nest=True))[:, total_mask]
    bright_pt_mask = bright_pt_mask | (la.norm(sky_vecs - pt_coord[:, None], axis=0) < pt_source_range)
    bright_pt_neighborhood_mask = bright_pt_neighborhood_mask | (la.norm(sky_vecs - pt_coord[:, None], axis=0) >= pt_source_neighborhood_range[0])
    bright_pt_neighborhood_mask = bright_pt_neighborhood_mask | (la.norm(sky_vecs - pt_coord[:, None], axis=0) <= pt_source_neighborhood_range[1])
AtNiA_sum = np.fromfile(AtNiA_filename, dtype='float64')
AtNiA_sum.shape = (npix_nonzero, npix_nonzero)
print "Computing PSFs...",
sys.stdout.flush()
timer = time.time()
raw_psf = AtNiAi.dotM(AtNiA_sum[:, bright_pt_mask])
smooth_psf = np.array([smoothing(raw_psf[:, i], smooth_scale) for i in range(raw_psf.shape[1])]).transpose()
psf = raw_psf - smooth_psf
print "%.1f min."%((time.time() - timer) / 60.)
sys.stdout.flush()
##clean using GSM
# good_mask = np.diagonal(AtNiA_sum)**-.5 < np.percentile(np.diagonal(AtNiA_sum)**-.5, 30)
# cold_mask = (~bright_pt_mask) #& (thetas < PI / 3)
# smooth_result = fake_solution_map * result[cold_mask&good_mask].dot(fake_solution_map[cold_mask&good_mask]) / fake_solution_map[cold_mask&good_mask].dot(fake_solution_map[cold_mask&good_mask])
# # cold_mask = np.abs(smooth_result) < np.percentile(np.abs(smooth_result), 65)

# ###traverse smooth scale: not making visible difference
# smooth_scales = PI / np.arange(30, 90, 10)
# ncol = len(smooth_scales)
# for icol, smooth_scale in enumerate(smooth_scales):
ncol = 1
icol = 0
smooth_result = smoothing(result * ~bright_pt_mask, smooth_scale)
# cold_mask = np.abs(smooth_result) < np.percentile(np.abs(smooth_result), 65)
cold_mask = (thetas[total_mask] < PI / 3) & (~bright_pt_mask)
good_mask = np.diagonal(AtNiA_sum)**-.5 < np.percentile(np.diagonal(AtNiA_sum)**-.5, 50)

Apsf = psf[cold_mask&good_mask]
bpsf = result[cold_mask&good_mask] - smooth_result[cold_mask&good_mask]
xpsf = la.inv(np.transpose(Apsf).dot(Apsf)).dot(np.transpose(Apsf).dot(bpsf))
fitpsf = Apsf.dot(xpsf)
cleaned_result = result - raw_psf.dot(xpsf)

plot_IQU(result / rescale, 'result', icol + 1, shape=(4, ncol), coord='CG')
plot_IQU((result - smooth_result) * (cold_mask&good_mask) / rescale, 'component trying to remove', ncol + icol + 1, shape=(4, ncol), coord='CG')
plot_IQU(cleaned_result / rescale, 'cleaned result', 2 * ncol + icol + 1, shape=(4, ncol), coord='CG')

clean_residuals = np.abs(cleaned_result - smooth_result)[cold_mask&good_mask]
bad_fitting_mask = np.abs(cleaned_result - smooth_result) * (cold_mask&good_mask) > np.percentile(clean_residuals, 90)

Apsf = psf[cold_mask&good_mask&bright_pt_neighborhood_mask&~bad_fitting_mask]
cumulated_xpsf = np.copy(xpsf)
for i in range(50):

    smooth_result = smoothing(cleaned_result * ~bright_pt_mask, smooth_scale)
    bpsf = (cleaned_result - smooth_result)[cold_mask&good_mask&bright_pt_neighborhood_mask&~bad_fitting_mask]
    xpsf = la.inv(np.transpose(Apsf).dot(Apsf)).dot(np.transpose(Apsf).dot(bpsf))
    fitpsf = Apsf.dot(xpsf)
    cleaned_result = cleaned_result - raw_psf.dot(xpsf)
    cumulated_xpsf += xpsf

plot_IQU(cleaned_result / rescale, 'iterated cleaned result', 3 * ncol + icol + 1, shape=(4, ncol), coord='CG')

# final_result = np.copy(cleaned_result)
# final_result[bright_pt_mask] += cumulated_xpsf
# final_result /= np.mean(np.sum(psf, axis=0))



########################resolution##########################
#############################################
################################################
low_nside = 16
low_npix = hpf.nside2npix(low_nside)
# low_theta, low_phi = hpf.pix2ang(low_nside, range(low_npix), nest=True)

low_mask = np.array(([True] + [False] * ((nside / low_nside)**2 - 1)) * low_npix)#low res mask to all pixels
low_sub_self_mask = total_mask[low_mask]
low_mask = low_mask&total_mask
low_sub_mask = low_mask[total_mask]# low res mask to result pixels
low_columns = np.arange(npix_nonzero)[low_sub_mask]

##resolution
def fwhm2(psf, verbose=False):
    spreaded = np.abs(psf) / np.max(np.abs(psf))
    fwhm_mask = spreaded >= .5
    return (np.sum(fwhm_mask) * 4 * PI / hpf.nside2npix(nside) / PI)**.5
    # masked_max_ind = np.argmax(spreaded[fwhm_mask])
    # fwhm_thetas = thetas[total_mask][fwhm_mask]
    # fwhm_phis = phis[total_mask][fwhm_mask]
    # #rotate angles to center around PI/2 0
    # fwhm_thetas, fwhm_phis = hpr.Rotator(rot=[fwhm_phis[masked_max_ind], PI/2-fwhm_thetas[masked_max_ind], 0], deg=False)(fwhm_thetas, fwhm_phis)
    # if verbose:
    #     print fwhm_thetas[masked_max_ind], fwhm_phis[masked_max_ind]#should print 1.57079632679 0.0 if rotation is working correctly
    #
    # fwhm_theta = max(fwhm_thetas) - min(fwhm_thetas)
    # phi_offset = fwhm_phis[masked_max_ind] - PI
    # fwhm_phis = (fwhm_phis - phi_offset)%TPI + phi_offset
    # fwhm_phi = max(fwhm_phis) - min(fwhm_phis)
    # return fwhm_theta, fwhm_phi

print "Computing partial PSF, predicted %.1fmin."%(8. * np.sum(low_sub_mask) / 50), datetime.datetime.now()
sys.stdout.flush()
timer = time.time()
partial_psfs = AtNiAi.dotM(AtNiA_sum[:, low_sub_mask])
print "%f minutes used" % (float(time.time() - timer) / 60.)
sys.stdout.flush()
map_resolution = np.zeros(low_npix) + np.pi / 10.
# map_resolution[low_sub_self_mask] = la.norm([fwhm(partial_psfs[:, c]) for c in range(partial_psfs.shape[1])], axis=-1)
map_resolution[low_sub_self_mask] = np.array([fwhm2(partial_psfs[:, c]) for c in range(partial_psfs.shape[1])])
smoothed_map_resolution = hpf.reorder(hp.smoothing(hpf.reorder(map_resolution, n2r=True), fwhm=2 * hpf.nside2resol(low_nside)), r2n=True)
map_resolution_full = hpf.get_interp_val(smoothed_map_resolution, thetas, phis, nest=True)
map_resolution_full.astype('float64').tofile(AtNiAi_filename + '_resolution_nside%i'%low_nside)
####

##error bar
print "Computing partial AtNiAi, predicted %.1fmin."%(8. * np.sum(low_sub_mask) / 50), datetime.datetime.now()
sys.stdout.flush()
timer = time.time()
partial_atniai = AtNiAi.dotM(np.eye(npix_nonzero)[:, low_sub_mask])
print "%f minutes used" % (float(time.time() - timer) / 60.)
sys.stdout.flush()
map_noise = np.zeros(low_npix) + 2 * reg**-.5
map_noise[low_sub_self_mask] = np.sum(partial_psfs * partial_atniai, axis=0)**.5#np.array([partial_atniai[low_columns[c], c] for c in range(partial_atniai.shape[1])])**.5
map_noise_full = hpf.get_interp_val(map_noise, thetas, phis, nest=True)
map_noise_full.astype('float64').tofile(AtNiAi_filename + '_noise_nside%i'%low_nside)

#####plot resolution and noise
plot_IQU(map_noise_full[total_mask], 'Uncertainty (K)', 1, shape=(2, 1), log=False, min=160, max=180, coord='CG')
plot_IQU(map_resolution_full[total_mask]*180./PI, 'Angular resolution (degree)', 2, shape=(2, 1), coord='CG', min=2., max=5., log=False)

###rough way: produces much better noise map?
# map_noise = np.diagonal(AtNiA_sum)**-.5 * absolute_noise_scale
snr_mask = map_resolution_full[total_mask] < 4 / 180. * PI#np.abs(map_noise / np.median(cleaned_result)) < 1

common_parkes_mask = snr_mask & (parkes_150[total_mask] > 0)
plot_IQU(np.abs(cleaned_result / rescale / parkes_150[total_mask] - 1) * 100 * common_parkes_mask, 'error percent', 1)
plot_IQU(np.abs(cleaned_result / rescale / gsm[total_mask] - 1) * 100 * common_parkes_mask, 'error percent', 1)

plt.subplot(1, 2, 1)
plt.title('Parkes chi^2')
plt.hist(((cleaned_result / rescale - parkes_150[total_mask]) / map_noise_full[total_mask])[common_parkes_mask]**2, bins=40)
plt.subplot(1, 2, 2)
plt.title('GSM chi^2')
plt.hist(((cleaned_result / rescale - gsm) / map_noise_full[total_mask])[snr_mask]**2, bins=40)
plt.show()