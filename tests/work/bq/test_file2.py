import h5lprec

# sizes
[nz, nproj, n] = [8, 1500, 2048]  # 16 max for RTX4000 float32
[ndark, nflat] = [1, 1]
[ntheta, nrho] = [2048, 4096] 
data_type = 'uint16'
center = 1024
fname = '/local/ssd/data/tmp/tomo_00001.h5'
clpthandle = h5lprec.H5LpRec(n, nproj, nz, ntheta, nrho, ndark, nflat, data_type,center,False)
clpthandle.recon_all(fname)
