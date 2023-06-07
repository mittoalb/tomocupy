import h5lprec
import h5py
import time
def create_h5():
    fid = h5py.File(fname,"w")
    data = fid.create_dataset("/exchange/data", proj_shape,
                                        chunks=(1, proj_shape[1], proj_shape[2]), dtype='u8')
    data_white = fid.create_dataset("/exchange/data_white", [nflat, proj_shape[1], proj_shape[2]],
                                        chunks=(1, proj_shape[1], proj_shape[2]), dtype='u8')

    data_dark = fid.create_dataset("/exchange/data_dark", [ndark, proj_shape[1], proj_shape[2]],
                                        chunks=(1, proj_shape[1], proj_shape[2]), dtype='u8')
    data[:] = 200
    data_white[:] = 1
    data_dark[:] = 0
    fid.close()

# sizes
[nz, nproj, n] = [8, 1500, 1024]  # 16 max for RTX4000 float32
[ndark, nflat] = [1, 1]
center = 1024
[ntheta, nrho] = [1024, 2048] 
data_type = 'uint16'
proj_shape = [nproj,1024,n]

fname = '/local/tmp/t.h5'
print(f'creating a fake h5 file {fname}, {proj_shape=}' )
create_h5() # create if not exist
print('Start reconstruction')

clpthandle = h5lprec.H5LpRec(n, nproj, nz, ntheta, nrho, ndark, nflat, data_type, center, False)
t = time.time()
clpthandle.recon_all(fname)
print(time.time()-t)
