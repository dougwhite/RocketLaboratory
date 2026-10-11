"""Strict geometry conversion of native stored BODY tuples, without timing claims.

Native world (X,Y,Z) -> simulator world (X,Z,Y), uu -> metres.
Native body axes forward/right/up -> simulator right/up/back.
Orientation is R_sim = P R_native B; angular velocity is axial: -P omega.
No capture, interpolation, actor-field fallback or hidden-state restoration.
"""
import math

WORLD_BASIS_P = ((1.,0.,0.),(0.,0.,1.),(0.,1.,0.))
LOCAL_BASIS_B = ((0.,0.,-1.),(1.,0.,0.),(0.,1.,0.))
UU_TO_METRES = .01
QUATERNION_NORM_ERROR_MAX = .002
STORED_FIELDS = ('rb_state_location_uu','rb_state_quaternion_wxyz',
    'rb_state_velocity_uu_s','rb_state_angular_velocity_rad_s','rb_state_time_s','rb_state_flags')


def _scalar(value,label):
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise ValueError(f'{label} must be a finite real number')
    try: result=float(value)
    except (OverflowError,ValueError) as error: raise ValueError(f'{label} is outside finite numeric range') from error
    if not math.isfinite(result): raise ValueError(f'{label} must be finite')
    return result


def _vector(value,count,label):
    if not isinstance(value,(list,tuple)) or len(value)!=count:
        raise ValueError(f'{label} requires exactly {count} numeric components')
    return [_scalar(x,label) for x in value]


def _matmul(a,b):
    return [[sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _matrix_wxyz(q):
    w,x,y,z=q
    return [[1-2*(y*y+z*z),2*(x*y-w*z),2*(x*z+w*y)],
            [2*(x*y+w*z),1-2*(x*x+z*z),2*(y*z-w*x)],
            [2*(x*z-w*y),2*(y*z+w*x),1-2*(x*x+y*y)]]


def _quaternion_xyzw(matrix):
    # Largest-diagonal branch stays conditioned near half turns. Matrix input
    # is already orthogonal by construction from the normalized source tuple.
    m=matrix
    candidates=[1+m[0][0]-m[1][1]-m[2][2],1-m[0][0]+m[1][1]-m[2][2],
                1-m[0][0]-m[1][1]+m[2][2],1+m[0][0]+m[1][1]+m[2][2]]
    axis=max(range(4),key=candidates.__getitem__);q=[0.]*4
    q[axis]=math.sqrt(max(0.,candidates[axis]))/2;denominator=4*q[axis]
    if denominator==0: raise ValueError('Degenerate converted orientation')
    if axis==3:
        q[:3]=[(m[2][1]-m[1][2])/denominator,(m[0][2]-m[2][0])/denominator,
               (m[1][0]-m[0][1])/denominator]
    else:
        j,k=(axis+1)%3,(axis+2)%3
        q[j]=(m[j][axis]+m[axis][j])/denominator
        q[k]=(m[k][axis]+m[axis][k])/denominator
        q[3]=(m[k][j]-m[j][k])/denominator
    length=math.hypot(*q);q=[x/length for x in q]
    # Deterministic antipodal representative; this is not temporal smoothing.
    first=next((value for value in (q[3],q[0],q[1],q[2]) if value!=0),1.)
    if first<0:q=[-x for x in q]
    return q


def position_uu_to_m(value):
    x,y,z=_vector(value,3,'native position')
    return [x*UU_TO_METRES,z*UU_TO_METRES,y*UU_TO_METRES]


def velocity_uu_s_to_m_s(value):
    x,y,z=_vector(value,3,'native linear velocity')
    return [x*UU_TO_METRES,z*UU_TO_METRES,y*UU_TO_METRES]


def angular_velocity_rad_s_to_sim(value):
    x,y,z=_vector(value,3,'native angular velocity')
    return [-x,-z,-y]


def orientation_wxyz_to_sim(value):
    """Return explicit raw/normalized provenance, basis and Bullet XYZW tuple.

    Preserve the source norm and raw components; reject zero or norm errors
    beyond the existing strict stored-body reader's .002 diagnostic boundary.
    This normalization is geometric arithmetic, not measurement repair.
    """
    raw=_vector(value,4,'native stored quaternion WXYZ');length=math.hypot(*raw)
    if length==0: raise ValueError('Zero native quaternion')
    if not math.isfinite(length) or abs(length-1)>QUATERNION_NORM_ERROR_MAX:
        raise ValueError('Native quaternion norm outside strict diagnostic tolerance')
    normalized=[x/length for x in raw]
    basis=_matmul(_matmul(WORLD_BASIS_P,_matrix_wxyz(normalized)),LOCAL_BASIS_B)
    q=_quaternion_xyzw(basis)
    return dict(source_quaternion_wxyz=raw,source_quaternion_norm=length,
        source_quaternion_norm_error=length-1,normalized_source_quaternion_wxyz=normalized,
        normalization_policy='explicit unit normalization after strict norm validation',
        source_components_changed_by_normalization=normalized!=raw,
        basis=basis,quaternion_xyzw=q,output_quaternion_norm=math.hypot(*q),
        source='native stored RBState quaternion WXYZ',native_phase_qualified=False)


def convert_stored_body(fields):
    """Convert six named stored fields only; retain the complete source tuple.

    Actor/current/old fields in a capture record are never substituted. Native
    time and flags are retained without interpretation. No history is created.
    """
    if not isinstance(fields,dict) or any(name not in fields for name in STORED_FIELDS):
        raise ValueError('Complete coherent named stored BODY tuple required')
    flags=fields['rb_state_flags']
    if type(flags) is not int or not 0<=flags<=2**32-1: raise ValueError('Stored native flags must be uint32')
    native_time=_scalar(fields['rb_state_time_s'],'stored native time')
    orientation=orientation_wxyz_to_sim(fields['rb_state_quaternion_wxyz'])
    position=_vector(fields['rb_state_location_uu'],3,'stored native position')
    velocity=_vector(fields['rb_state_velocity_uu_s'],3,'stored native linear velocity')
    angular=_vector(fields['rb_state_angular_velocity_rad_s'],3,'stored native angular velocity')
    return dict(schema='noodle.native.coordinates.v1',position=position_uu_to_m(position),
        rotation=orientation['quaternion_xyzw'],velocity=velocity_uu_s_to_m_s(velocity),
        angular_velocity=angular_velocity_rad_s_to_sim(angular),orientation_provenance=orientation,
        source_tuple=dict(zip(STORED_FIELDS,(position,orientation['source_quaternion_wxyz'],velocity,angular,native_time,flags))),
        source_fields=list(STORED_FIELDS),source='native stored RBState only',
        units=dict(position='metres',velocity='metres/second',angular_velocity='radians/second',rotation='XYZW'),
        native_phase_qualified=False,hidden_state_restored=False,physics_parity_qualified=False)
