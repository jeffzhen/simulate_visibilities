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
Qs = []
A_fns = []
AtNiA_fns = []
data_fns = []

INSTRUMENTS = ['miteor']#, 'miteor_compact']#'paper']#, 'miteor']
valid_npixs = {'miteor': 41832}#{'paper': 14896, 'miteor': 10428, 'miteor_compact': 12997}
datatags = {'paper': '_lstbineven_avg4', 'miteor': '_2016_01_20_avg2_unpollock', 'miteor_compact': '_2016_01_20_avg'}
vartags = {'paper': '_lstbineven_avg4', 'miteor': '_2016_01_20_avg2_unpollock', 'miteor_compact': '_2016_01_20_avgx100'}
datadirs = {'paper': '/home/omniscope/data/PAPER/lstbin_fg/even/', 'miteor': '/home/omniscope/data/GSM_data/absolute_calibrated_data/', 'miteor_compact': '/home/omniscope/data/GSM_data/absolute_calibrated_data/'}
bnsides = {'paper': 64, 'miteor': 256, 'miteor_compact': 256}
noise_scales = {'paper': 10., 'miteor': 1., 'miteor_compact': 1.}

for instrument in INSTRUMENTS:
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
freqs = np.zeros(len(data_fns))
for i, (data_fn, Q) in enumerate(zip(data_fns, Qs)):
    data_file = np.load(data_fn)
    freqs[i] = data_file['freq']
    ubls[Q] = data_file['ubls']
    tlists[Q] = data_file['tlist']
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
child_masks = {}
for instrument in INSTRUMENTS:
    sub_pixel_files[instrument] = np.load(pixel_dir + 'pixel_scheme_%i.npz'%valid_npixs[instrument])
    child_masks[instrument] = sub_pixel_files[instrument]['child_mask']


def sol2map(sol, std=False):
    solx = sol[:total_valid_npix]
    full_sol = np.zeros(npix)
    if std:
        full_sol[valid_pix_mask] = solx / (sizes)**.5
    else:
        full_sol[valid_pix_mask] = solx / sizes
    return full_sol[final_index]

def plot_IQU(solution, title, col, shape=(1, 1), coord='C', std=False, min=0, max=4, nside_out=None):
    # Es=solution[np.array(final_index).tolist()].reshape((4, len(final_index)/4))
    # I = Es[0] + Es[3]
    # Q = Es[0] - Es[3]
    # U = Es[1] + Es[2]
    I = sol2map(solution, std=std)
    if nside_out != None:
        I = hpf.ud_grade(I, nside_out=nside_out, order_in='NESTED', order_out='NESTED')
    plotcoordtmp = coord
    hpv.mollview(np.log10(I), min=min, max=max, coord=plotcoordtmp, title=title, nest=True, sub=(shape[0], shape[1], col))
    if col == shape[0] * shape[1]:
        plt.show()



##################
###start calculations
###############
n_iter = 0
max_iter = 3

###re-weighting iteration
while n_iter < max_iter:
    AtNiA_filename = datadirs['miteor'] + 'mega_AtNiA_n%i_iter%i'%(total_valid_npix, n_iter)

    AtNid_filename = datadirs['miteor'] + 'mega_AtNid_n%i_iter%i'%(total_valid_npix, n_iter)
    AtNisimd_filename = datadirs['miteor'] + 'mega_AtNisimd_n%i_iter%i'%(total_valid_npix, n_iter)
    weight_filename = datadirs['miteor'] + 'mega_weight_n%i_iter%i'%(total_valid_npix, n_iter)
    if os.path.isfile(AtNiA_filename) and os.path.isfile(AtNid_filename) and os.path.isfile(AtNisimd_filename):
        AtNiA_sum = np.fromfile(AtNiA_filename, dtype='float64')
        AtNiA_sum.shape = (total_valid_npix, total_valid_npix)
        AtNidata_sum = np.fromfile(AtNid_filename, dtype='float64')
        if n_iter == 0:
            weights = np.array([(freq / standard_freq)**-2.5 for freq in freqs])
        else:
            weights = np.fromfile(weight_filename, dtype='float64')
    else:
        AtNidata_sum = np.zeros(total_valid_npix, dtype='float64')
        # AtNiptdata_sum = np.zeros((2, total_valid_npix), dtype='float64')
        AtNisimdata_sum = np.zeros(total_valid_npix, dtype='float64')
        AtNiA_sum = np.zeros((total_valid_npix, total_valid_npix), dtype='float64')
        weights = np.zeros(len(data_fns), dtype='float64')#synchrotron scaling, [divide data by weight and mult Ni and AtNiA by weight**2], or [multiply A by weight and AtNiA by weight**2]

        for i, (Q, data_fn, A_fn, AtNiA_fn) in enumerate(zip(Qs, data_fns, A_fns, AtNiA_fns)):
            child_mask = None
            for instrument in INSTRUMENTS:
                if datatags[instrument] in data_fn:
                    child_mask = child_masks[instrument]
                    valid_npix = valid_npixs[instrument]
                    break
            if child_mask is None:
                raise Exception('Logic error instrument not found.')
            print Q, np.sum(child_mask), len(child_mask)
            sys.stdout.flush()

            data_file = np.load(data_fn)
            data = data_file['data']
            nUBL = len(ubls[Q])
            nt = len(tlists[Q])
            Ni = data_file['Ni'] / noise_scales[instrument]**2


            # nUBL = int(A_fn.split(Q + 'A_dI_u')[1].split('_')[0])
            At = np.zeros((total_valid_npix, len(data)), dtype='float32')
            At[child_mask] = (np.fromfile(A_fn, dtype='float32').reshape((len(data)/2, valid_npix + 4*nUBL, 2))[:, :valid_npix]).transpose((1, 2, 0)).reshape((valid_npix, len(data)))
            if n_iter == 0:
                weights[i] = (freqs[i] / standard_freq)**-2.5
            else:
                Ax = At.transpose().dot(result)
                weights[i] = np.sum(data * Ni * Ax) / np.sum(Ax * Ni * Ax)
            AtNidata_sum += At.dot(data * Ni) * weights[i]
            AtNiA_sum[np.ix_(child_mask, child_mask)] += np.fromfile(AtNiA_fn, dtype='float64').reshape((valid_npix, valid_npix)) * weights[i]**2 / noise_scales[instrument]**2

            if n_iter == 0 or n_iter == max_iter - 1:
                # for n, pt_data in enumerate(data_file['psdata']):
                #     AtNiptdata_sum[n] += At.dot(pt_data * Ni) * weights[i]
                AtNisimdata_sum += At.dot(data_file['simdata'] * Ni) * weights[i]
        # if n_iter == 0:
            # AtNiA_sum0 = np.copy(AtNiA_sum)
            # AtNidata_sum0 = np.copy(AtNidata_sum)
            # #AtNiptdata_sum0 = np.copy(AtNiptdata_sum)
            # AtNisimdata_sum0 = np.copy(AtNisimdata_sum)

        AtNiA_sum.tofile(AtNiA_filename)
        AtNidata_sum.tofile(AtNid_filename)
        AtNisimdata_sum.tofile(AtNisimd_filename)
        weights.tofile(weight_filename)
    if n_iter != 0:
        plt.plot(sorted(freqs), weights[np.argsort(freqs)])
    if n_iter != max_iter - 1:
        for reg in 10.**np.arange(-6, -5, .5):
            AtNiAi_filename = datadirs['miteor'] + 'mega_AtNiAi_n%i_iter%i_reg%.3e'%(total_valid_npix, n_iter, reg)
            if os.path.isfile(AtNiAi_filename):
                AtNiAi = sv.InverseCholeskyMatrix.fromfile(AtNiAi_filename, total_valid_npix, 'float64')
                break
            else:
                print "trying", reg, datetime.datetime.now()
                sys.stdout.flush()
                timer = time.time()
                try:
                    add_diag_in_place(AtNiA_sum, np.ones(total_valid_npix) * reg)
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
if max_iter != 1:
    plt.show()


for reg in 10.**np.arange(-4, -2, .5):
    AtNiAi_filename = datadirs['miteor'] + 'mega_AtNiAi_n%i_iter%i_reg%.3e'%(total_valid_npix, max_iter - 1, reg)
    if os.path.isfile(AtNiAi_filename):
        AtNiAi = sv.InverseCholeskyMatrix.fromfile(AtNiAi_filename, total_valid_npix, 'float64')
        break
    else:
        print "trying", reg, datetime.datetime.now()
        sys.stdout.flush()
        timer = time.time()
        try:
            add_diag_in_place(AtNiA_sum, np.ones(total_valid_npix) * reg)
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

#####################################
####error analysis and spectral index change
#####################################
errors = {}
chi2s = {}
fits = {}
Nis = {}
datas = {}
amp_fits = {}
print "###Error analysis####"
sys.stdout.flush()
for i, (Q, data_fn, A_fn, AtNiA_fn) in enumerate(zip(Qs, data_fns, A_fns, AtNiA_fns)):
    child_mask = None
    for instrument in INSTRUMENTS:
        if datatags[instrument] in data_fn:
            child_mask = child_masks[instrument]
            valid_npix = valid_npixs[instrument]
            break
    if child_mask is None:
        raise Exception('Logic error instrument not found.')
    print Q, np.sum(child_mask), len(child_mask)
    sys.stdout.flush()

    data_file = np.load(data_fn)
    freq = data_file['freq']
    data = data_file['data']
    nUBL = len(ubls[Q])
    nt = len(tlists[Q])
    Ni = data_file['Ni'] / noise_scales[instrument]**2

    A = np.zeros((len(data), total_valid_npix), dtype='float32')
    A[:, child_mask] = (np.fromfile(A_fn, dtype='float32').reshape((len(data)/2, valid_npix + 4*nUBL, 2))[:, :valid_npix]).transpose((2, 0, 1)).reshape((len(data), valid_npix))

    fit = A.dot(result) * weights[i]
    error = fit - data
    chi2 = error**2 * Ni

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


    amp_fit = np.array([d.dot(ni * ft) / ft.dot(ni * ft) for d, ft, ni in zip(reshape_data(data), reshape_data(fit), reshape_data(Ni))]) * weights[i]


    errors[Q] = get_complex_data(error)
    chi2s[Q] = get_complex_data(chi2, chi2=True)
    fits[Q] = get_complex_data(fit)
    Nis[Q] = get_complex_data(Ni)
    datas[Q] = get_complex_data(data)
    amp_fits[Q] = amp_fit

    ubl_len = la.norm(ubls[Q], axis=-1)
    ubl_sort = np.argsort(ubl_len)
    plt.subplot(2, 2, 1)
    plt.plot(sorted(ubl_len), la.norm(la.norm(errors[Q], axis=-1), axis=-1)[ubl_sort], label=Q)
    plt.subplot(2, 2, 2)
    plt.plot(sorted(ubl_len), np.sum(np.sum(np.sum(chi2s[Q], axis=-1), axis=-1), axis=0)[ubl_sort], label=Q)
    plt.subplot(2, 2, 3)
    plt.plot((tlists[Q] - 5)%24 + 5, la.norm(reshape_data(error), axis=-1), label=Q)
    plt.subplot(2, 2, 4)
    plt.plot((tlists[Q] - 5)%24 + 5, np.sum(reshape_data(chi2), axis=-1), label=Q)
plt.legend()
plt.show()

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
        spectral, spectral_error = fit_power(tmp_freqs[1:] * 1e6, tmp_amps[1:])
        spectral_index_list.append([t, spectral, spectral_error])
    t += t_grid_size
plt.legend()
plt.xlabel('Freq (MHz)')
plt.ylabel('Amplitude ratio')

plt.subplot(1, 2, 2)
spectral_index_list = np.array(spectral_index_list)
plt.errorbar(spectral_index_list[:, 0], spectral_index_list[:, 1], fmt='g+', yerr=spectral_index_list[:, 2])
plt.xlabel('LST (hour)')
plt.ylabel('Spectral index')
plt.show()

########################
#####plot stuff in mollwide
#########################
rescale = (nside_standard / nside)**2
plot_IQU(result / rescale, '+'.join(INSTRUMENTS), 1, shape=(3, 4), coord='CG')
plot_IQU(sim_result / rescale, 'noiseless simulation', 5, shape=(3, 4), coord='CG')
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
parkes_150 = fitsio.read("/home/omniscope/data/polarized foregrounds/parkes_150mhz.bin")[0]
parkes_150[:, :-1] = np.roll(parkes_150[:, :-1], 180, axis=1)[:, ::-1]
parkes_150[:, -1] = parkes_150[:, 0]
parkes_150[parkes_150 > 7.e3] = -1e-9
parkes_150[parkes_150 <= 0] = -1e-9
parkes_150 = sv.equirectangular2heapix(parkes_150, nside_standard)
parkes_150[parkes_150 <= 0] = np.nan
hpv.mollview(np.log10(parkes_150), nest=True, min=0, max=4, sub=(3, 4, 3), title='parkes150MHz')

####GSM####
plot_IQU(fake_solution_map, 'GSM', 4, shape=(3, 4), coord='CG')





def smoothing(m, fwhm, nest=True):
    if fwhm <= 0:
        return m
    nside = hpf.npix2nside(len(m))
    if nest:
        return hp.smoothing(m[hpf.ring2nest(nside, np.arange(hpf.nside2npix(nside)))], fwhm=fwhm)[hpf.nest2ring(nside, np.arange(hpf.nside2npix(nside)))]
    else:
        return hp.smoothing(m, fwhm=fwhm)

clean = ('miteor' in INSTRUMENTS)

if clean:#take abt 10 min, not quite working. ringing seems not caused by wiener filter??
    bright_points = {'cyg':{'ra': '19:59:28.3', 'dec': '40:44:02'},
                        'cas':{'ra': '23:23:26', 'dec': '58:48:00'}}
    pt_source_range = PI / 45
    pt_source_neighborhood_range = PI / 9
    smooth_scale = PI / 45
    bright_pt_mask = np.zeros(total_valid_npix, dtype=bool)
    bright_pt_neighborhood_mask = np.zeros(total_valid_npix, dtype=bool)
    for source in bright_points.keys():
        bright_points[source]['body'] = ephem.FixedBody()
        bright_points[source]['body']._ra = bright_points[source]['ra']
        bright_points[source]['body']._dec = bright_points[source]['dec']
        theta = PI / 2 - bright_points[source]['body']._dec
        phi = bright_points[source]['body']._ra
        pt_coord = np.array([np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta)])
        bright_pt_mask = bright_pt_mask | (la.norm(hpf.pix2vec(nside, np.arange(hpf.nside2npix(nside)), nest=True) - pt_coord[:, None], axis=0) < pt_source_range)
        bright_pt_neighborhood_mask = bright_pt_neighborhood_mask | (la.norm(hpf.pix2vec(nside, np.arange(hpf.nside2npix(nside)), nest=True) - pt_coord[:, None], axis=0) < pt_source_neighborhood_range)
    print "Computing PSFs..."
    sys.stdout.flush()
    psf = AtNiAi.dotM(AtNiA_sum[:, bright_pt_mask])

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
    cold_mask = (thetas < PI / 3) & (~bright_pt_mask)
    good_mask = np.diagonal(AtNiA_sum)**-.5 < np.percentile(np.diagonal(AtNiA_sum)**-.5, 50)

    Apsf = psf[cold_mask&good_mask]
    bpsf = result[cold_mask&good_mask] - smooth_result[cold_mask&good_mask]
    xpsf = la.inv(np.transpose(Apsf).dot(Apsf)).dot(np.transpose(Apsf).dot(bpsf))
    fitpsf = Apsf.dot(xpsf)
    cleaned_result = result - psf.dot(xpsf)

    plot_IQU(result / rescale, 'result', icol + 1, shape=(4, ncol), coord='CG')
    plot_IQU((result - smooth_result) * (cold_mask&good_mask) / rescale, 'component trying to remove', ncol + icol + 1, shape=(4, ncol), coord='CG')
    plot_IQU(cleaned_result / rescale, 'cleaned result', 2 * ncol + icol + 1, shape=(4, ncol), coord='CG')


    Apsf = psf[cold_mask&good_mask&bright_pt_neighborhood_mask]
    for i in range(50):

        smooth_result = smoothing(cleaned_result * ~bright_pt_mask, smooth_scale)
        bpsf = (cleaned_result - smooth_result)[cold_mask&good_mask&bright_pt_neighborhood_mask]
        xpsf = la.inv(np.transpose(Apsf).dot(Apsf)).dot(np.transpose(Apsf).dot(bpsf))
        fitpsf = Apsf.dot(xpsf)
        cleaned_result = cleaned_result - psf.dot(xpsf)

    plot_IQU(cleaned_result / rescale, 'iterated cleaned result', 3 * ncol + icol + 1, shape=(4, ncol), coord='CG')



    # #traditional clean: benifit very slight, barely noticeable
    # bright_pt_indices = np.arange(npix)[bright_pt_mask]
    # clean_stop = np.percentile(np.abs(result/sizes), 80)
    # niter = 0
    # clean_residue = np.copy(cleaned_result)
    # clean_accumulate = np.zeros_like(clean_residue)
    # step_size = .01
    # while np.max(np.abs((clean_residue / sizes)[bright_pt_mask])) > clean_stop and niter < 1000:
    #     niter += 1
    #     ipix_bright = np.argmax(np.abs((clean_residue / sizes)[bright_pt_mask]))
    #     ipix = bright_pt_indices[ipix_bright]
    #     origin_ipix_bright = np.argmax(np.abs(psf[ipix]))
    #     origin_ipix = bright_pt_indices[origin_ipix_bright]
    #     print niter, ipix_bright, (clean_residue[ipix] / sizes[ipix])
    #
    #     delta = clean_residue[ipix] * step_size
    #     clean_residue -= delta / psf[ipix, origin_ipix_bright] * psf[:, ipix_bright]
    #     clean_accumulate[origin_ipix] += delta / psf[ipix, origin_ipix_bright]
    #
    # plot_IQU(result / rescale, 'result', 1, shape=(3, 1), coord='CG')
    # plot_IQU(cleaned_result / rescale, '5xcleaned result', 2, shape=(3, 1), coord='CG')
    # plot_IQU(clean_residue / rescale, 'traditional cleaned', 3, shape=(3, 1), coord='CG')