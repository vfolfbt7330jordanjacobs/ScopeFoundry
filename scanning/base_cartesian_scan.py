'''
Created on Feb 4, 2016

@author: Edward Barnard
'''

from ScopeFoundry import Measurement
from ScopeFoundry.helper_funcs import sibling_path, load_qt_ui_file
import numpy as np
import pyqtgraph as pg
import time
from ScopeFoundry import h5_io
from qtpy import QtCore
from ScopeFoundry import LQRange
import os

def ijk_zigzag_generator(dims, axis_order=(0,1,2)):
    """3D zig-zag scan pattern generator with arbitrary fast axis order"""

    ax0, ax1, ax2 = axis_order
    
    for i_ax0 in range( dims[ax0] ):
        zig_or_zag0 = (1,-1)[i_ax0 % 2]
        for i_ax1 in range( dims[ax1] )[::zig_or_zag0]:
            zig_or_zag1 = (1,-1)[(i_ax0+i_ax1) % 2]
            for i_ax2 in range( dims[ax2] )[::zig_or_zag1]:
            
                ijk = [0,0,0]
                ijk[ax0] = i_ax0
                ijk[ax1] = i_ax1
                ijk[ax2] = i_ax2
                
                yield tuple(ijk)
    return

class BaseCartesian2DScan(Measurement):
    name = "base_cartesian_2Dscan"
    
    def __init__(self, app, h_limits=(-1,1), v_limits=(-1,1), h_unit='', v_unit=''):
        self.h_limits = h_limits
        self.v_limits = v_limits
        self.h_unit = h_unit
        self.v_unit = v_unit
        Measurement.__init__(self, app)
        
    def setup(self):
        self.ui_filename = sibling_path(__file__,"cart_scan_base.ui")
        self.ui = load_qt_ui_file(self.ui_filename)
        #self.ui.show()
        self.ui.setWindowTitle(self.name)

        self.display_update_period = 0.010 #seconds

        #connect events        

        # local logged quantities
        lq_params = dict(dtype=float, vmin=self.h_limits[0],vmax=self.h_limits[1], ro=False, unit=self.h_unit )
        self.h0 = self.settings.New('h0',  initial=0, **lq_params  )
        self.h1 = self.settings.New('h1',  initial=1, **lq_params  )
        lq_params = dict(dtype=float, vmin=self.v_limits[0],vmax=self.v_limits[1], ro=False, unit=self.h_unit )
        self.v0 = self.settings.New('v0',  initial=0, **lq_params  )
        self.v1 = self.settings.New('v1',  initial=1, **lq_params  )

        lq_params = dict(dtype=float, vmin=1e-9, vmax=abs(self.h_limits[1]-self.h_limits[0]), ro=False, unit=self.h_unit )
        self.dh = self.settings.New('dh', initial=0.1, **lq_params)
        self.dh.spinbox_decimals = 3
        lq_params = dict(dtype=float, vmin=1e-9, vmax=abs(self.v_limits[1]-self.v_limits[0]), ro=False, unit=self.v_unit )
        self.dv = self.settings.New('dv', initial=0.1, **lq_params)
        self.dv.spinbox_decimals = 3
        
        self.Nh = self.settings.New('Nh', initial=11, vmin=1, dtype=int, ro=False)
        self.Nv = self.settings.New('Nv', initial=11, vmin=1, dtype=int, ro=False)
        
        self.scan_type = self.settings.New('scan_type', dtype=str, initial='raster',
                                                  choices=('raster', 'serpentine', 'trace_retrace', 
                                                           'ortho_raster', 'ortho_trace_retrace'))
        
        self.continuous_scan = self.settings.New("continuous_scan", dtype=bool, initial=False)
        self.settings.New('save_h5', dtype=bool, initial=True, ro=False)
        
        self.settings.New('show_previous_scans', dtype=bool, initial=True)
        
        #update Nh, Nv and other scan parameters when changes to inputs are made 
        #for lqname in 'h0 h1 v0 v1 dh dv'.split():
        #    self.logged_quantities[lqname].updated_value.connect(self.compute_scan_params)
        self.h_range = LQRange(self.h0, self.h1, self.dh, self.Nh)
        self.h_range.updated_range.connect(self.compute_scan_params)

        self.v_range = LQRange(self.v0, self.v1, self.dv, self.Nv)
        self.v_range.updated_range.connect(self.compute_scan_params) #update other scan parameters when changes to inputs are made

        self.scan_type.updated_value.connect(self.compute_scan_params)
        
        #connect events
        self.ui.start_pushButton.clicked.connect(self.start)
        self.ui.interrupt_pushButton.clicked.connect(self.interrupt)

        self.h0.connect_bidir_to_widget(self.ui.h0_doubleSpinBox)
        self.h1.connect_bidir_to_widget(self.ui.h1_doubleSpinBox)
        self.v0.connect_bidir_to_widget(self.ui.v0_doubleSpinBox)
        self.v1.connect_bidir_to_widget(self.ui.v1_doubleSpinBox)
        self.dh.connect_bidir_to_widget(self.ui.dh_doubleSpinBox)
        self.dv.connect_bidir_to_widget(self.ui.dv_doubleSpinBox)
        self.Nh.connect_bidir_to_widget(self.ui.Nh_doubleSpinBox)
        self.Nv.connect_bidir_to_widget(self.ui.Nv_doubleSpinBox)
        self.scan_type.connect_bidir_to_widget(self.ui.scan_type_comboBox)
        
        self.progress.connect_bidir_to_widget(self.ui.progress_doubleSpinBox)
        #self.progress.updated_value[str].connect(self.ui.xy_scan_progressBar.setValue)
        #self.progress.updated_value.connect(self.tree_progressBar.setValue)

        self.initial_scan_setup_plotting = False
        self.display_image_map = np.zeros(self.scan_shape, dtype=float)
        self.scan_specific_setup()
        

        self.add_operation('clear_previous_scans', self.clear_previous_scans)

    def compute_scan_params(self):
        self.log.debug('compute_scan_params')
        # Don't recompute if a scan is running!
        if self.is_measuring():
            return # maybe raise error

        #self.h_array = self.h_range.array #np.arange(self.h0.val, self.h1.val, self.dh.val, dtype=float)
        #self.v_array = self.v_range.array #np.arange(self.v0.val, self.v1.val, self.dv.val, dtype=float)
        
        #self.Nh.update_value(len(self.h_array))
        #self.Nv.update_value(len(self.v_array))
        
        self.range_extent = [self.h0.val, self.h1.val, self.v0.val, self.v1.val]

        #self.corners =  [self.h_array[0], self.h_array[-1], self.v_array[0], self.v_array[-1]]
        self.corners = self.range_extent
        
        self.imshow_extent = [self.h0.val - 0.5*self.dh.val,
                              self.h1.val + 0.5*self.dh.val,
                              self.v0.val - 0.5*self.dv.val,
                              self.v1.val + 0.5*self.dv.val]
                
        
        # call appropriate scan generator to determine scan size, don't compute scan arrays yet
        getattr(self, "gen_%s_scan" % self.scan_type.val)(gen_arrays=False)
    
    def compute_scan_arrays(self):
        print("params")
        self.compute_scan_params()
        print("gen_arrays")
        getattr(self, "gen_%s_scan" % self.scan_type.val)(gen_arrays=True)
    
    def create_empty_scan_arrays(self):
        self.scan_h_positions = np.zeros(self.Npixels, dtype=float)
        self.scan_v_positions = np.zeros(self.Npixels, dtype=float)
        self.scan_slow_move   = np.zeros(self.Npixels, dtype=bool)
        self.scan_index_array = np.zeros((self.Npixels, 3), dtype=int)

    def pre_run(self):
        # set all logged quantities read only
        for lqname in "h0 h1 v0 v1 dh dv Nh Nv".split():
            self.settings.as_dict()[lqname].change_readonly(True)
    
    
    
    
    
    def post_run(self):
            # set all logged quantities writable
            for lqname in "h0 h1 v0 v1 dh dv Nh Nv".split():
                self.settings.as_dict()[lqname].change_readonly(False)

    def clear_qt_attr(self, attr_name):
        if hasattr(self, attr_name):
            attr = getattr(self, attr_name)
            attr.deleteLater()
            del attr
            
    def setup_figure(self):
        self.compute_scan_params()
            
        self.clear_qt_attr('graph_layout')
        self.graph_layout=pg.GraphicsLayoutWidget(border=(100,100,100))
        self.ui.plot_groupBox.layout().addWidget(self.graph_layout)
        
        self.clear_qt_attr('img_plot')
        self.img_plot = self.graph_layout.addPlot()

        self.img_items = []
        
        
        self.img_item = pg.ImageItem()
        self.img_items.append(self.img_item)
        
        self.img_plot.addItem(self.img_item)
        self.img_plot.showGrid(x=True, y=True)
        self.img_plot.setAspectLocked(lock=True, ratio=1)

        self.hist_lut = pg.HistogramLUTItem()
        self.graph_layout.addItem(self.hist_lut)

        
        #self.clear_qt_attr('current_stage_pos_arrow')
        self.current_stage_pos_arrow = pg.ArrowItem()
        self.current_stage_pos_arrow.setZValue(100)
        self.img_plot.addItem(self.current_stage_pos_arrow)
        
        #self.stage = self.app.hardware_components['dummy_xy_stage']
        if hasattr(self, 'stage'):
            self.stage.x_position.updated_value.connect(self.update_arrow_pos, QtCore.Qt.UniqueConnection)
            self.stage.y_position.updated_value.connect(self.update_arrow_pos, QtCore.Qt.UniqueConnection)
            
            self.stage.x_position.connect_bidir_to_widget(self.ui.x_doubleSpinBox)
            self.stage.y_position.connect_bidir_to_widget(self.ui.y_doubleSpinBox)

        
        self.graph_layout.nextRow()
        self.pos_label = pg.LabelItem(justify='right')
        self.pos_label.setText("=====")
        self.graph_layout.addItem(self.pos_label)

        self.scan_roi = pg.ROI([0,0],[1,1], movable=True)
        self.scan_roi.addScaleHandle([1, 1], [0, 0])
        self.scan_roi.addScaleHandle([0, 0], [1, 1])
        self.update_scan_roi()
        self.scan_roi.sigRegionChangeFinished.connect(self.mouse_update_scan_roi)
        
        self.img_plot.addItem(self.scan_roi)        
        for lqname in 'h0 h1 v0 v1 dh dv'.split():
            self.settings.as_dict()[lqname].updated_value.connect(self.update_scan_roi)
                    
        self.img_plot.scene().sigMouseMoved.connect(self.mouseMoved)
    
    def mouse_update_scan_roi(self):
        x0,y0 =  self.scan_roi.pos()
        w, h =  self.scan_roi.size()
        #print x0,y0, w, h
        self.h0.update_value(x0+self.dh.val)
        self.h1.update_value(x0+w-self.dh.val)
        self.v0.update_value(y0+self.dv.val)
        self.v1.update_value(y0+h-self.dv.val)
        self.compute_scan_params()
        self.update_scan_roi()
        
    def update_scan_roi(self):
        self.log.debug("update_scan_roi")
        x0, x1, y0, y1 = self.imshow_extent
        self.scan_roi.blockSignals(True)
        self.scan_roi.setPos( (x0, y0, 0))
        self.scan_roi.setSize( (x1-x0, y1-y0, 0))
        self.scan_roi.blockSignals(False)
        
    def update_arrow_pos(self):
        x = self.stage.x_position.val
        y = self.stage.y_position.val
        self.current_stage_pos_arrow.setPos(x,y)
    
    def update_display(self):
        self.log.debug('update_display')
        if self.initial_scan_setup_plotting:
            if self.settings['show_previous_scans']:
                self.img_item = pg.ImageItem()
                self.img_items.append(self.img_item)
                self.img_plot.addItem(self.img_item)
                self.hist_lut.setImageItem(self.img_item)
    
            self.img_item.setImage(self.display_image_map[0,:,:])
            x0, x1, y0, y1 = self.imshow_extent
            self.log.debug('update_display set bounds {} {} {} {}'.format(x0, x1, y0, y1))
            self.img_item_rect = QtCore.QRectF(x0, y0, x1-x0, y1-y0)
            self.img_item.setRect(self.img_item_rect)
            self.log.debug('update_display set bounds {}'.format(self.img_item_rect))
            
            self.initial_scan_setup_plotting = False
        else:
            #if self.settings.scan_type.val in ['raster']
            kk, jj, ii = self.current_scan_index
            self.img_item.setImage(self.display_image_map[kk,:,:].T, autoRange=False, autoLevels=False)
            self.img_item.setRect(self.img_item_rect) # Important to set rectangle after setImage for non-square pixels
            self.hist_lut.imageChanged(autoLevel=True)
            
    def clear_previous_scans(self):
        #current_img = img_items.pop()
        for img_item in self.img_items[:-1]:
            print('removing', img_item)
            self.img_plot.removeItem(img_item)  
            img_item.deleteLater()
    
        self.img_items = [self.img_item,]
    
    def mouseMoved(self,evt):
        mousePoint = self.img_plot.vb.mapSceneToView(evt)
        #print mousePoint
        
        #self.pos_label_text = "H {:+02.2f} um [{}], V {:+02.2f} um [{}]: {:1.2e} Hz ".format(
        #                mousePoint.x(), ii, mousePoint.y(), jj,
        #                self.count_rate_map[jj,ii] 
        #                )


        self.pos_label.setText(
            "H {:+02.2f} um [{}], V {:+02.2f} um [{}]: {:1.2e} Hz".format(
                        mousePoint.x(), 0, mousePoint.y(), 0, 0))

    def scan_specific_setup(self):
        "subclass this function to setup additional logged quantities and gui connections"
        pass
        #self.stage = self.app.hardware.dummy_xy_stage
        
        #self.app.hardware_components['dummy_xy_stage'].x_position.connect_bidir_to_widget(self.ui.x_doubleSpinBox)
        #self.app.hardware_components['dummy_xy_stage'].y_position.connect_bidir_to_widget(self.ui.y_doubleSpinBox)
        
        #self.app.hardware_components['apd_counter'].int_time.connect_bidir_to_widget(self.ui.int_time_doubleSpinBox)
       
       
       
        # logged quantities
        # connect events
        
    
    def pre_scan_setup(self):
        print(self.name, "pre_scan_setup not implemented")
        # hardware
        # create data arrays
        # update figure
    
    def post_scan_cleanup(self):
        print(self.name, "post_scan_setup not implemented")
    
    @property
    def h_array(self):
        return self.h_range.array

    @property
    def v_array(self):
        return self.v_range.array
    
    #### Scan Generators
    def gen_raster_scan(self, gen_arrays=True):
        self.Npixels = self.Nh.val*self.Nv.val
        self.scan_shape = (1, self.Nv.val, self.Nh.val)
        
        if gen_arrays:
            #print "t0", time.time() - t0
            self.create_empty_scan_arrays()            
            #print "t1", time.time() - t0
            
#             t0 = time.time()
#             pixel_i = 0
#             for jj in range(self.Nv.val):
#                 #print "tjj", jj, time.time() - t0
#                 self.scan_slow_move[pixel_i] = True
#                 for ii in range(self.Nh.val):
#                     self.scan_v_positions[pixel_i] = self.v_array[jj]
#                     self.scan_h_positions[pixel_i] = self.h_array[ii]
#                     self.scan_index_array[pixel_i,:] = [0, jj, ii] 
#                     pixel_i += 1
#             print "for loop raster gen", time.time() - t0
             
            t0 = time.time()
             
            H, V = np.meshgrid(self.h_array, self.v_array)
            self.scan_h_positions[:] = H.flat
            self.scan_v_positions[:] = V.flat
            
            II,JJ = np.meshgrid(np.arange(self.Nh.val), np.arange(self.Nv.val))
            self.scan_index_array[:,1] = JJ.flat
            self.scan_index_array[:,2] = II.flat
            #self.scan_v_positions
            print("array flatten raster gen", time.time() - t0)
            
        
    def gen_serpentine_scan(self, gen_arrays=True):
        self.Npixels = self.Nh.val*self.Nv.val
        self.scan_shape = (1, self.Nv.val, self.Nh.val)

        if gen_arrays:
            self.create_empty_scan_arrays()
            pixel_i = 0
            for jj in range(self.Nv.val):
                self.scan_slow_move[pixel_i] = True
                
                if jj % 2: #odd lines
                    h_line_indicies = range(self.Nh.val)[::-1]
                else:       #even lines -- traverse in opposite direction
                    h_line_indicies = range(self.Nh.val)            
        
                for ii in h_line_indicies:            
                    self.scan_v_positions[pixel_i] = self.v_array[jj]
                    self.scan_h_positions[pixel_i] = self.h_array[ii]
                    self.scan_index_array[pixel_i,:] = [0, jj, ii]                 
                    pixel_i += 1
                
    def gen_trace_retrace_scan(self, gen_arrays=True):
        self.Npixels = 2*self.Nh.val*self.Nv.val
        self.scan_shape = (2, self.Nv.val, self.Nh.val)

        if gen_arrays:
            self.create_empty_scan_arrays()
            pixel_i = 0
            for jj in range(self.Nv.val):
                self.scan_slow_move[pixel_i] = True     
                for kk, step in [(0,1),(1,-1)]: # trace kk =0, retrace kk=1
                    h_line_indicies = range(self.Nh.val)[::step]
                    for ii in h_line_indicies:            
                        self.scan_v_positions[pixel_i] = self.v_array[jj]
                        self.scan_h_positions[pixel_i] = self.h_array[ii]
                        self.scan_index_array[pixel_i,:] = [kk, jj, ii]                 
                        pixel_i += 1
    
    def gen_ortho_raster_scan(self, gen_arrays=True):
        self.Npixels = 2*self.Nh.val*self.Nv.val
        self.scan_shape = (2, self.Nv.val, self.Nh.val)

        if gen_arrays:
            self.create_empty_scan_arrays()
            pixel_i = 0
            for jj in range(self.Nv.val):
                self.scan_slow_move[pixel_i] = True
                for ii in range(self.Nh.val):
                    self.scan_v_positions[pixel_i] = self.v_array[jj]
                    self.scan_h_positions[pixel_i] = self.h_array[ii]
                    self.scan_index_array[pixel_i,:] = [0, jj, ii] 
                    pixel_i += 1
            for ii in range(self.Nh.val):
                self.scan_slow_move[pixel_i] = True
                for jj in range(self.Nv.val):
                    self.scan_v_positions[pixel_i] = self.v_array[jj]
                    self.scan_h_positions[pixel_i] = self.h_array[ii]
                    self.scan_index_array[pixel_i,:] = [1, jj, ii] 
                    pixel_i += 1
    
    def gen_ortho_trace_retrace_scan(self, gen_arrays=True):
        print("gen_ortho_trace_retrace_scan")
        self.Npixels = 4*len(self.h_array)*len(self.v_array) 
        self.scan_shape = (4, self.Nv.val, self.Nh.val)                        
        
        if gen_arrays:
            self.create_empty_scan_arrays()
            pixel_i = 0
            for jj in range(self.Nv.val):
                self.scan_slow_move[pixel_i] = True     
                for kk, step in [(0,1),(1,-1)]: # trace kk =0, retrace kk=1
                    h_line_indicies = range(self.Nh.val)[::step]
                    for ii in h_line_indicies:            
                        self.scan_v_positions[pixel_i] = self.v_array[jj]
                        self.scan_h_positions[pixel_i] = self.h_array[ii]
                        self.scan_index_array[pixel_i,:] = [kk, jj, ii]                 
                        pixel_i += 1
            for ii in range(self.Nh.val):
                self.scan_slow_move[pixel_i] = True     
                for kk, step in [(2,1),(3,-1)]: # trace kk =2, retrace kk=3
                    v_line_indicies = range(self.Nv.val)[::step]
                    for jj in v_line_indicies:            
                        self.scan_v_positions[pixel_i] = self.v_array[jj]
                        self.scan_h_positions[pixel_i] = self.h_array[ii]
                        self.scan_index_array[pixel_i,:] = [kk, jj, ii]                 
                        pixel_i += 1
                    
class BaseCartesian2DSlowScan(BaseCartesian2DScan):

    name = "base_cartesian_2Dscan"

    def run(self):
        S = self.settings
        
        
        #Hardware
        # self.apd_counter_hc = self.app.hardware_components['apd_counter']
        # self.apd_count_rate = self.apd_counter_hc.apd_count_rate
        # self.stage = self.app.hardware_components['dummy_xy_stage']

        # Data File
        # H5

        # Compute data arrays
        self.compute_scan_arrays()
        
        self.initial_scan_setup_plotting = True
        
        self.display_image_map = np.zeros(self.scan_shape, dtype=float)


        while not self.interrupt_measurement_called:        
            try:
                # h5 data file setup
                self.t0 = time.time()
                if self.settings['save_h5']:
                    
                    h5fname = os.path.join(
                        self.app.settings['save_dir'],
                        "%i_%s.h5" % (self.t0, self.name))
                    
                    self.h5_file = h5_io.h5_base_file(self.app, h5fname)
                          
                    self.h5_file.attrs['time_id'] = self.t0
                    H = self.h5_meas_group  =  h5_io.h5_create_measurement_group(self, self.h5_file)
                
                    #create h5 data arrays
                    H['h_array'] = self.h_array
                    H['v_array'] = self.v_array
                    H['range_extent'] = self.range_extent
                    H['corners'] = self.corners
                    H['imshow_extent'] = self.imshow_extent
                    H['scan_h_positions'] = self.scan_h_positions
                    H['scan_v_positions'] = self.scan_v_positions
                    H['scan_slow_move'] = self.scan_slow_move
                    H['scan_index_array'] = self.scan_index_array
                
                self.pre_scan_setup()
                
                # start scan
                self.pixel_i = 0
                
                self.pixel_time = np.zeros(self.scan_shape, dtype=float)
                if self.settings['save_h5']:
                    self.pixel_time_h5 = H.create_dataset(name='pixel_time', shape=self.scan_shape, dtype=float)            
                
                self.move_position_start(self.scan_h_positions[0], self.scan_v_positions[0])
                
                for self.pixel_i in range(self.Npixels):                
                    if self.interrupt_measurement_called: break
                    
                    i = self.pixel_i
                    
                    self.current_scan_index = self.scan_index_array[i]
                    kk, jj, ii = self.current_scan_index
                    
                    h,v = self.scan_h_positions[i], self.scan_v_positions[i]
                    
                    if self.pixel_i == 0:
                        dh = 0
                        dv = 0
                    else:
                        dh = self.scan_h_positions[i] - self.scan_h_positions[i-1] 
                        dv = self.scan_v_positions[i] - self.scan_v_positions[i-1] 
                    
                    if self.scan_slow_move[i]:
                        self.move_position_slow(h,v, dh, dv)
                        if self.settings['save_h5']:    
                            self.h5_file.flush() # flush data to file every slow move
                        #self.app.qtapp.ProcessEvents()
                        time.sleep(0.01)
                    else:
                        self.move_position_fast(h,v, dh, dv)
                    
                    self.pos = (h,v)
                    # each pixel:
                    # acquire signal and save to data array
                    pixel_t0 = time.time()
                    self.pixel_time[kk, jj, ii] = pixel_t0
                    if self.settings['save_h5']:
                        self.pixel_time_h5[kk, jj, ii] = pixel_t0
                    self.collect_pixel(self.pixel_i, kk, jj, ii)
                    S['progress'] = 100.0*self.pixel_i / (self.Npixels)
            finally:
                self.post_scan_cleanup()
                if self.settings['save_h5'] and hasattr(self, 'h5_file'):
                    self.h5_file.close()
                if not self.continuous_scan.val:
                    break
                
    def move_position_start(self, x,y):
        self.stage.x_position.update_value(x)
        self.stage.y_position.update_value(y)
    
    def move_position_slow(self, x,y, dx, dy):
        self.stage.x_position.update_value(x)
        self.stage.y_position.update_value(y)
        
    def move_position_fast(self, x,y, dx, dy):
        self.stage.x_position.update_value(x)
        self.stage.y_position.update_value(y)
        #x = self.stage.settings['x_position']
        #y = self.stage.settings['y_position']        
        #x = self.stage.settings.x_position.read_from_hardware()
        #y = self.stage.settings.y_position.read_from_hardware()
        #print(x,y)
        

    def collect_pixel(self, pixel_num, k, j, i):
        # collect data
        # store in arrays        
        print(self.name, "collect_pixel", pixel_num, k,j,i, "not implemented")
    



class TestCartesian2DSlowScan(BaseCartesian2DSlowScan):
    name='test_cart_2d_slow_scan'
    
    def __init__(self, app):
        BaseCartesian2DSlowScan.__init__(self, app, h_limits=(0,100), v_limits=(0,100), h_unit="um", v_unit="um")        
    
    def setup(self):
        BaseCartesian2DSlowScan.setup(self)
        self.settings.New('pixel_time', initial=0.001, unit='s', si=False, spinbox_decimals=5)
        
    
    def pre_scan_setup(self):
        self.display_update_period = 0.050 #seconds
        
        self.stage = self.app.hardware['dummy_xy_stage']
        if self.settings['save_h5']:
            self.test_data = self.h5_meas_group.create_dataset('test_data', self.scan_shape, dtype=float)
        
        self.prev_px = time.time()
         
    def post_scan_cleanup(self):
        print("post_scan_cleanup")
        
    def collect_pixel(self, pixel_i, k,j,i):
        #print pixel_i, k,j,i
        t0 = time.time()
        #px_data = np.random.rand()
        #px_data = t0 - self.prev_px
        x0,y0 = self.pos
        x_set = self.stage.settings['x_position']
        y_set = self.stage.settings['y_position']
        x_hw = self.stage.settings.x_position.read_from_hardware(send_signal=False)
        y_hw = self.stage.settings.y_position.read_from_hardware(send_signal=False)
        if np.abs(x_hw - x0) > 1:
            self.log.debug('='*60)
            self.log.debug('pos      {} {}'.format(x0, y0))
            self.log.debug('settings {} {}'.format(x_set, y_set))
            self.log.debug('hw       {} {}'.format(x_hw, y_hw))            
            self.log.debug('settings value delta {} {}'.format(x_set-x0, y_set-y0))
            self.log.debug('read_hw  value delta {} {}'.format(x_hw-x0, y_hw-y0))
            self.log.debug('='*60)
        
        x = x_hw
        y = y_hw
        
        px_data = np.sinc((x-50)*0.05)**2 * np.sinc(0.05*(y-50))**2 #+ 0.05*np.random.random()
        #px_data = (x-xhw)**2 + ( y-yhw)**2
        #if px_data > 1:
        #    print('hw', x, xhw, y, yhw)
        self.display_image_map[k,j,i] = px_data
        if self.settings['save_h5']:
            self.test_data[k,j,i] = px_data 
        time.sleep(self.settings['pixel_time'])
        #self.prev_px = t0
