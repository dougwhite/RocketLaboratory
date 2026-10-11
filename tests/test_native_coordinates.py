import copy
import math
import struct
import unittest

from rocketlaboratory.instrument import native_coordinates as coordinates


def multiply(a,b):
    w,x,y,z=a;v,i,j,k=b
    return [w*v-x*i-y*j-z*k,w*i+x*v+y*k-z*j,w*j-x*k+y*v+z*i,w*k+x*j-y*i+z*v]


def axis_quaternion(axis,angle):
    length=math.hypot(*axis);s=math.sin(angle/2)/length
    return [math.cos(angle/2),*(value*s for value in axis)]


def rotate_wxyz(q,v):
    # Independent Hamilton-product oracle rather than the converter's matrices.
    result=multiply(multiply(q,[0,*v]),[q[0],-q[1],-q[2],-q[3]])
    return result[1:]


def rotate_output(output,v):
    x,y,z,w=output['quaternion_xyzw'];return rotate_wxyz([w,x,y,z],v)


def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
def p(v):return [v[0],v[2],v[1]]


class NativeCoordinatesTests(unittest.TestCase):
    def assertVector(self,a,b):
        for x,y in zip(a,b):self.assertAlmostEqual(x,y,places=12)

    def test_units_polar_and_axial_axis_mapping(self):
        self.assertEqual(coordinates.position_uu_to_m([100,200,300]),[1,3,2])
        self.assertEqual(coordinates.velocity_uu_s_to_m_s([-200,0,500]),[-2,5,0])
        self.assertEqual(coordinates.angular_velocity_rad_s_to_sim([1,2,3]),[-1,-3,-2])
        for basis in ([1,0,0],[0,1,0],[0,0,1]):
            self.assertVector(coordinates.angular_velocity_rad_s_to_sim(basis),[-x for x in p(basis)])

    def test_native_identity_has_correct_local_nose_and_world_heading(self):
        result=coordinates.orientation_wxyz_to_sim([1,0,0,0])
        self.assertVector(rotate_output(result,[0,0,-1]),[1,0,0])
        self.assertVector(rotate_output(result,[1,0,0]),[0,0,1])
        self.assertVector(rotate_output(result,[0,1,0]),[0,1,0])
        self.assertNotEqual(result['quaternion_xyzw'],[0,0,0,1])
        self.assertEqual(result['basis'],[[0,0,-1],[0,1,0],[1,0,0]])

    def test_quarter_turn_yaw_pitch_roll_local_axes(self):
        half=math.sqrt(.5)
        # Native Rotator-style signs: yaw about +Z, pitch about -Y,
        # roll about -X. These are algebraic basis tests, not native telemetry.
        cases=[([half,0,0,half],[0,0,1],[-1,0,0],[0,1,0]),
               ([half,0,-half,0],[0,1,0],[0,0,1],[-1,0,0]),
               ([half,-half,0,0],[1,0,0],[0,-1,0],[0,0,1])]
        for q,forward,right,up in cases:
            result=coordinates.orientation_wxyz_to_sim(q)
            self.assertVector(rotate_output(result,[0,0,-1]),forward)
            self.assertVector(rotate_output(result,[1,0,0]),right)
            self.assertVector(rotate_output(result,[0,1,0]),up)

    def test_arbitrary_rotations_preserve_every_body_axis_covariantly(self):
        for axis in ([1,0,0],[0,1,0],[0,0,1],[1,2,-3]):
            for angle in (-math.pi,-.71,0,.37,math.pi-1e-9,math.pi):
                q=axis_quaternion(axis,angle);converted=coordinates.orientation_wxyz_to_sim(q)
                for native,sim in (([1,0,0],[0,0,-1]),([0,1,0],[1,0,0]),([0,0,1],[0,1,0])):
                    self.assertVector(rotate_output(converted,sim),p(rotate_wxyz(q,native)))

    def test_angular_cross_product_covariance_under_reflection(self):
        for omega,linear in (([1,2,3],[4,-5,6]),([-2,.3,4],[1,8,-9]),([0,0,1],[1,0,0])):
            expected=coordinates.velocity_uu_s_to_m_s(cross(omega,linear))
            measured=cross(coordinates.angular_velocity_rad_s_to_sim(omega),coordinates.velocity_uu_s_to_m_s(linear))
            self.assertVector(measured,expected)
            # Omitting the axial sign would reverse the induced rotation.
            self.assertVector(cross(p(omega),coordinates.velocity_uu_s_to_m_s(linear)),[-x for x in expected])

    def test_finite_world_angular_increment_composition_covariance(self):
        initial=axis_quaternion([1,2,3],.87);start=coordinates.orientation_wxyz_to_sim(initial)
        x,y,z,w=start['quaternion_xyzw'];startq=[w,x,y,z]
        for native_axis in ([1,0,0],[0,1,0],[0,0,1],[2,-1,3]):
            for angle in (-.2,.003,1.3):
                native_new=multiply(axis_quaternion(native_axis,angle),initial)
                converted=coordinates.orientation_wxyz_to_sim(native_new)
                sim_axis=coordinates.angular_velocity_rad_s_to_sim(native_axis)
                expected=multiply(axis_quaternion(sim_axis,angle),startq)
                for v in ([1,0,0],[0,1,0],[0,0,-1]):
                    self.assertVector(rotate_output(converted,v),rotate_wxyz(expected,v))

    def test_float_norm_is_recorded_normalized_and_input_preserved(self):
        # Synthetic float32 measurement, not private user capture telemetry.
        q=list(struct.unpack('<4f',struct.pack('<4f',*axis_quaternion([1,3,-2],.013))))
        before=q.copy();result=coordinates.orientation_wxyz_to_sim(q)
        self.assertEqual(q,before);self.assertEqual(result['source_quaternion_wxyz'],q)
        self.assertEqual(result['source_quaternion_norm'],math.hypot(*q))
        self.assertTrue(result['source_components_changed_by_normalization'])
        self.assertAlmostEqual(math.hypot(*result['normalized_source_quaternion_wxyz']),1,places=15)
        self.assertAlmostEqual(result['output_quaternion_norm'],1,places=15)
        self.assertFalse(result['native_phase_qualified'])
        for scale in (.999,1.001):
            self.assertAlmostEqual(coordinates.orientation_wxyz_to_sim([scale,0,0,0])['source_quaternion_norm'],scale)

    def test_antipodal_and_half_turn_stability(self):
        for q in ([1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1],axis_quaternion([1,-2,3],math.pi-1e-12)):
            a=coordinates.orientation_wxyz_to_sim(q);b=coordinates.orientation_wxyz_to_sim([-x for x in q])
            self.assertVector(a['quaternion_xyzw'],b['quaternion_xyzw'])
            self.assertAlmostEqual(a['output_quaternion_norm'],1,places=15)

    def test_malformed_nonfinite_zero_and_grossly_nonunit_quaternions_reject(self):
        invalid=(None,'1000',{},[1,0,0],[1,0,0,0,0],[True,0,0,0],[0,0,0,0],
                 [math.nan,0,0,0],[math.inf,0,0,0],[1e-300,0,0,0],[2,0,0,0],[10**1000,0,0,0])
        for value in invalid:
            with self.subTest(value=str(value)[:40]),self.assertRaises(ValueError):coordinates.orientation_wxyz_to_sim(value)
        for method in (coordinates.position_uu_to_m,coordinates.velocity_uu_s_to_m_s,coordinates.angular_velocity_rad_s_to_sim):
            for invalid in ([0,0],[True,0,0],[0,math.inf,0],'123'):
                with self.assertRaises(ValueError):method(invalid)

    def test_complete_named_stored_body_only_preserves_raw_source_and_rejects_fallback(self):
        fields=dict(rb_state_location_uu=[100,200,300],rb_state_quaternion_wxyz=[1,0,0,0],
            rb_state_velocity_uu_s=[400,500,600],rb_state_angular_velocity_rad_s=[1,2,3],rb_state_time_s=0,rb_state_flags=7,
            location_uu=[999,999,999],current_rb_state_location_uu=[888,888,888])
        original=copy.deepcopy(fields);result=coordinates.convert_stored_body(fields)
        self.assertEqual(result['position'],[1,3,2]);self.assertEqual(result['velocity'],[4,6,5])
        self.assertEqual(result['angular_velocity'],[-1,-3,-2]);self.assertEqual(fields,original)
        self.assertEqual(result['source_tuple'],{key:fields[key] for key in coordinates.STORED_FIELDS})
        for key in coordinates.STORED_FIELDS:
            changed=copy.deepcopy(fields);del changed[key]
            with self.subTest(key=key),self.assertRaises(ValueError):coordinates.convert_stored_body(changed)
        for flags in (True,-1,2**32,7.):
            changed=copy.deepcopy(fields);changed['rb_state_flags']=flags
            with self.assertRaises(ValueError):coordinates.convert_stored_body(changed)
        for time in (True,float('nan')):
            changed=copy.deepcopy(fields);changed['rb_state_time_s']=time
            with self.assertRaises(ValueError):coordinates.convert_stored_body(changed)
        for flag in ('native_phase_qualified','hidden_state_restored','physics_parity_qualified'):self.assertFalse(result[flag])


if __name__=='__main__':unittest.main()
