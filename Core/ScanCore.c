#include "ScanCore.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdarg.h>
#include <time.h>
#include <limits.h>

#define SC_MAX_VERTICES 1000000
#define SC_MAX_TRIANGLES 1000000
#define SC_MAX_LINES 10000

static int fail(char *e, size_t n, const char *fmt, ...) {
    if (e && n) { va_list a; va_start(a, fmt); vsnprintf(e, n, fmt, a); va_end(a); }
    return 0;
}
static SCPoint3 sub(SCPoint3 a, SCPoint3 b) { return (SCPoint3){a.x-b.x,a.y-b.y,a.z-b.z}; }
static SCPoint3 cross(SCPoint3 a, SCPoint3 b) {
    return (SCPoint3){a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};
}
static double dot(SCPoint3 a, SCPoint3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
static double norm(SCPoint3 a) { return sqrt(dot(a,a)); }
static int finite_point(SCPoint3 a) { return isfinite(a.x)&&isfinite(a.y)&&isfinite(a.z); }

int sc_depth_unproject(float u,float v,float depth,float fx,float fy,
                       float cx,float cy,const float *m,SCPoint3 *world) {
    if(!m||!world||!isfinite(u)||!isfinite(v)||!isfinite(depth)||depth<=0||
       !isfinite(fx)||!isfinite(fy)||fx<=0||fy<=0||!isfinite(cx)||!isfinite(cy)) return 0;
    for(int i=0;i<16;i++) if(!isfinite(m[i])) return 0;
    /* A CV pixel has Y down / Z forward; an ARKit camera has Y up / Z back. */
    double x=((double)u-cx)*depth/fx, y=-((double)v-cy)*depth/fy, z=-depth;
    SCPoint3 p={m[0]*x+m[4]*y+m[8]*z+m[12],
                m[1]*x+m[5]*y+m[9]*z+m[13],
                m[2]*x+m[6]*y+m[10]*z+m[14]};
    if(!finite_point(p)||fabs(p.x)>1000||fabs(p.y)>1000||fabs(p.z)>1000) return 0;
    *world=p; return 1;
}
static uint8_t cloud_channel(double v) {
    return (uint8_t)lround(fmax(0,fmin(255,v)));
}
void sc_camera_rgb(uint8_t y,uint8_t cb,uint8_t cr,int full,int bt709,uint8_t *rgb) {
    if(!rgb) return;
    double yy=y, b=(double)cb-128, r=(double)cr-128;
    if(!full) { yy=((double)y-16)*255/219; b*=255.0/224; r*=255.0/224; }
    rgb[0]=cloud_channel(yy+(bt709?1.5748:1.402)*r);
    rgb[1]=cloud_channel(yy-(bt709?0.187324:0.344136)*b-(bt709?0.468124:0.714136)*r);
    rgb[2]=cloud_channel(yy+(bt709?1.8556:1.772)*b);
}
static int validate_cloud(const char *path,const SCCloudPoint *p,int32_t n,char *e,size_t en) {
    if(!path||!path[0]||!p||n<=0||n>500000) return fail(e,en,"Point cloud is empty or exceeds 500,000 points.");
    for(int32_t i=0;i<n;i++) if(!isfinite(p[i].x)||!isfinite(p[i].y)||!isfinite(p[i].z)||
        fabs(p[i].x)>1000||fabs(p[i].y)>1000||fabs(p[i].z)>1000||p[i].confidence>2)
        return fail(e,en,"Invalid point cloud coordinate or confidence.");
    return 1;
}
int sc_write_cloud_ply(const char *path,const SCCloudPoint *p,int32_t n,char *e,size_t en) {
    if(!validate_cloud(path,p,n,e,en)) return 0;
    FILE *f=fopen(path,"wb"); if(!f) return fail(e,en,"Cannot create point cloud PLY.");
    int ok=fprintf(f,"ply\nformat binary_little_endian 1.0\n"
        "comment ShapeMeasure ARKit sceneDepth point cloud\n"
        "comment units metres; right-handed ARKit world; Y up; no geographic CRS\n"
        "comment confidence 0 low 1 medium 2 high; voxel sampled\n"
        "element vertex %d\nproperty float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "property uchar confidence\nend_header\n",n)>0;
    for(int32_t i=0;ok&&i<n;i++) {
        unsigned char record[16]; float coords[3]={p[i].x,p[i].y,p[i].z};
        for(int j=0;j<3;j++) {
            uint32_t bits; memcpy(&bits,&coords[j],4);
            for(int k=0;k<4;k++) record[j*4+k]=(unsigned char)(bits>>(8*k));
        }
        record[12]=p[i].red; record[13]=p[i].green; record[14]=p[i].blue; record[15]=p[i].confidence;
        ok=fwrite(record,1,sizeof(record),f)==sizeof(record);
    }
    if(fclose(f)!=0) ok=0;
    if(!ok) { remove(path); return fail(e,en,"Could not finish writing point cloud PLY."); }
    return 1;
}
int sc_write_cloud_xyz(const char *path,const SCCloudPoint *p,int32_t n,char *e,size_t en) {
    if(!validate_cloud(path,p,n,e,en)) return 0;
    FILE *f=fopen(path,"wb"); if(!f) return fail(e,en,"Cannot create point cloud XYZ.");
    int ok=1;
    for(int32_t i=0;ok&&i<n;i++) ok=fprintf(f,"%.9g %.9g %.9g\n",p[i].x,p[i].y,p[i].z)>0;
    if(fclose(f)!=0) ok=0;
    if(!ok) { remove(path); return fail(e,en,"Could not finish writing point cloud XYZ."); }
    return 1;
}

typedef struct { double x,y; } XY;
static int compare_xy(const void *pa, const void *pb) {
    const XY *a=pa,*b=pb;
    if(a->x<b->x) return -1;
    if(a->x>b->x) return 1;
    return (a->y>b->y)-(a->y<b->y);
}
static double turn(XY a, XY b, XY c) { return (b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x); }
/* Returns a CCW convex hull; footprint deliberately includes concave voids. */
static XY *hull(const SCPoint3 *v, int32_t n, int32_t *hn) {
    XY *p=malloc((size_t)n*sizeof(*p));
    XY *h=malloc((size_t)(2*n+1)*sizeof(*h));
    if(!p||!h) { free(p); free(h); return NULL; }
    for(int32_t i=0;i<n;i++) p[i]=(XY){v[i].x,-v[i].z};
    qsort(p,(size_t)n,sizeof(*p),compare_xy);
    int32_t u=0;
    for(int32_t i=0;i<n;i++) if(u==0||compare_xy(&p[i],&p[u-1])) p[u++]=p[i];
    int32_t k=0;
    for(int32_t i=0;i<u;i++) { while(k>=2&&turn(h[k-2],h[k-1],p[i])<=0) k--; h[k++]=p[i]; }
    int32_t t=k+1;
    for(int32_t i=u-2;i>=0;i--) { while(k>=t&&turn(h[k-2],h[k-1],p[i])<=0) k--; h[k++]=p[i]; }
    if(k>1) k--;
    free(p); *hn=k; return h;
}
typedef struct { int32_t a,b,face; int8_t direction; } Edge;
static int compare_edge(const void *pa,const void *pb) {
    const Edge *a=pa,*b=pb;
    if(a->a!=b->a) return (a->a>b->a)-(a->a<b->a);
    return (a->b>b->b)-(a->b<b->b);
}
static int32_t root(int32_t *parents,int32_t p) {
    while(parents[p]!=p) { parents[p]=parents[parents[p]]; p=parents[p]; }
    return p;
}

int sc_analyze(const SCPoint3 *v,int32_t nv,const SCTriangle *tri,int32_t nt,
               double scale,SCMetrics *m,char *e,size_t en) {
    if(e&&en) e[0]=0;
    if(!v||!tri||!m||nv<3||nt<1||nv>SC_MAX_VERTICES||nt>SC_MAX_TRIANGLES)
        return fail(e,en,"Invalid mesh or geometry exceeds the supported size.");
    if(!isfinite(scale)||scale<=0||scale>1e9) return fail(e,en,"A finite positive metric scale is required.");
    memset(m,0,sizeof(*m)); m->volume_m3=NAN;
    unsigned char *used=calloc((size_t)nv,1);
    Edge *edges=malloc((size_t)nt*3*sizeof(*edges));
    int32_t *parents=malloc((size_t)nt*sizeof(*parents));
    if(!used||!edges||!parents) { free(used); free(edges); free(parents); return fail(e,en,"Not enough memory."); }
    int ok=1;
    for(int32_t i=0;i<nv;i++) if(!finite_point(v[i])||fabs(v[i].x)>1e9||fabs(v[i].y)>1e9||fabs(v[i].z)>1e9) {
        ok=fail(e,en,"Nonfinite or out-of-range mesh coordinate."); break;
    }
    for(int32_t i=0;ok&&i<nt;i++) {
        int32_t idx[3]={tri[i].a,tri[i].b,tri[i].c}; parents[i]=i;
        for(int j=0;j<3;j++) if(idx[j]<0||idx[j]>=nv) { ok=fail(e,en,"Triangle index outside the mesh."); break; }
        if(!ok) break;
        if(idx[0]==idx[1]||idx[1]==idx[2]||idx[2]==idx[0]) { ok=fail(e,en,"Degenerate triangle."); break; }
        for(int j=0;j<3;j++) {
            used[idx[j]]=1;
            int32_t a=idx[j],b=idx[(j+1)%3];
            edges[3*i+j]=(Edge){a<b?a:b,a<b?b:a,i,(int8_t)(a<b?1:-1)};
        }
    }
    for(int32_t i=0;ok&&i<nv;i++) if(!used[i]) ok=fail(e,en,"Mesh contains unreferenced vertices. Clean the mesh first.");
    free(used);
    if(!ok) { free(edges); free(parents); return 0; }
    m->min_x=m->max_x=v[0].x; m->min_y=m->max_y=v[0].y; m->min_z=m->max_z=v[0].z;
    for(int32_t i=1;i<nv;i++) {
        m->min_x=fmin(m->min_x,v[i].x); m->max_x=fmax(m->max_x,v[i].x);
        m->min_y=fmin(m->min_y,v[i].y); m->max_y=fmax(m->max_y,v[i].y);
        m->min_z=fmin(m->min_z,v[i].z); m->max_z=fmax(m->max_z,v[i].z);
    }
    SCPoint3 center={(m->min_x+m->max_x)/2,(m->min_y+m->max_y)/2,(m->min_z+m->max_z)/2};
    double signed_volume=0;
    for(int32_t i=0;i<nt;i++) {
        SCPoint3 a=sub(v[tri[i].a],center),b=sub(v[tri[i].b],center),c=sub(v[tri[i].c],center);
        double area=norm(cross(sub(b,a),sub(c,a)))/2;
        if(!isfinite(area)||area<=0) { free(edges); free(parents); return fail(e,en,"Degenerate triangle or numerical overflow."); }
        m->surface_m2+=area*scale*scale;
        signed_volume+=dot(a,cross(b,c))/6;
    }
    qsort(edges,(size_t)nt*3,sizeof(*edges),compare_edge);
    for(int32_t i=0;i<nt*3;) {
        int32_t j=i+1; int dirs=edges[i].direction;
        while(j<nt*3&&compare_edge(&edges[i],&edges[j])==0) {
            dirs+=edges[j].direction;
            parents[root(parents,edges[j].face)]=root(parents,edges[i].face); j++;
        }
        if(j-i==1) m->boundary_edges++;
        else if(j-i!=2) m->nonmanifold_edges++;
        else if(dirs!=0) m->winding_errors++;
        i=j;
    }
    for(int32_t i=0;i<nt;i++) if(root(parents,i)==i) m->components++;
    free(edges); free(parents);
    m->watertight=(m->boundary_edges==0&&m->nonmanifold_edges==0&&m->winding_errors==0);
    m->volume_valid=m->watertight&&m->components==1;
    if(m->volume_valid) m->volume_m3=fabs(signed_volume)*scale*scale*scale;
    m->width_m=(m->max_x-m->min_x)*scale;
    m->height_m=(m->max_y-m->min_y)*scale;
    m->depth_m=(m->max_z-m->min_z)*scale;
    m->vertex_count=nv; m->triangle_count=nt;
    int32_t hn=0; XY *h=hull(v,nv,&hn);
    if(!h) return fail(e,en,"Not enough memory for footprint.");
    double area=0,perim=0;
    for(int32_t i=0;i<hn;i++) { XY a=h[i],b=h[(i+1)%hn]; area+=a.x*b.y-b.x*a.y; perim+=hypot(a.x-b.x,a.y-b.y); }
    free(h);
    m->footprint_m2=fabs(area)/2*scale*scale; m->footprint_perimeter_m=perim*scale;
    if(!isfinite(m->surface_m2)||!isfinite(m->footprint_m2)||!isfinite(m->width_m)||
       !isfinite(m->depth_m)||!isfinite(m->height_m)||(m->volume_valid&&!isfinite(m->volume_m3)))
        return fail(e,en,"Measurement overflow.");
    return 1;
}

/* All on-disk formats use explicit byte order; no packed C structs. */
typedef struct { unsigned char *d; size_t n,capacity; int ok; } Bytes;
static Bytes bytes(void) { return (Bytes){NULL,0,0,1}; }
static void put(Bytes *b,const void *p,size_t n) {
    if(!b->ok) return;
    if(n>SIZE_MAX-b->n) { b->ok=0; return; }
    if(b->n+n>b->capacity) {
        size_t c=b->n+n;
        if(c<SIZE_MAX/2) c=c*2+128;
        unsigned char *d=realloc(b->d,c);
        if(!d) { b->ok=0; return; } b->d=d; b->capacity=c;
    }
    if(n) memcpy(b->d+b->n,p,n);
    b->n+=n;
}
static void u8(Bytes *b,unsigned v) { unsigned char c=(unsigned char)v; put(b,&c,1); }
static void le16(Bytes *b,uint16_t v) { u8(b,v); u8(b,v>>8); }
static void le32(Bytes *b,uint32_t v) { for(int i=0;i<4;i++) u8(b,v>>(8*i)); }
static void be32(Bytes *b,uint32_t v) { for(int i=3;i>=0;i--) u8(b,v>>(8*i)); }
static void f64(Bytes *b,double v) { uint64_t x; memcpy(&x,&v,8); for(int i=0;i<8;i++) u8(b,(unsigned)(x>>(8*i))); }
static void textf(Bytes *b,const char *fmt,...) {
    va_list a,copy; va_start(a,fmt); va_copy(copy,a);
    int n=vsnprintf(NULL,0,fmt,copy); va_end(copy);
    if(n<0) { b->ok=0; va_end(a); return; }
    char *s=malloc((size_t)n+1);
    if(!s) { b->ok=0; va_end(a); return; }
    vsnprintf(s,(size_t)n+1,fmt,a); va_end(a); put(b,s,(size_t)n); free(s);
}
static void json_string(Bytes *b,const char *s) {
    u8(b,'"');
    for(const unsigned char *p=(const unsigned char *)s;*p;p++) {
        if(*p=='"'||*p=='\\') { u8(b,'\\'); u8(b,*p); }
        else if(*p<32) textf(b,"\\u%04x",*p);
        else u8(b,*p);
    }
    u8(b,'"');
}
static void csv_string(Bytes *b,const char *s) {
    u8(b,'"'); for(;*s;s++) { if(*s=='"') u8(b,'"'); u8(b,(unsigned char)*s); } u8(b,'"');
}
static int write_bytes(const char *dir,const char *name,Bytes *b,char *e,size_t en) {
    char path[4096];
    if(!b->ok||snprintf(path,sizeof(path),"%s/%s",dir,name)>=(int)sizeof(path)) return fail(e,en,"Export size or path limit exceeded.");
    FILE *f=fopen(path,"wb"); if(!f) return fail(e,en,"Cannot create %s.",name);
    int ok=fwrite(b->d,1,b->n,f)==b->n;
    if(fclose(f)!=0) ok=0;
    if(!ok) return fail(e,en,"Cannot finish writing %s.",name);
    return 1;
}
const char *sc_local_crs(void) {
    return "LOCAL_CS[\"Object local metric coordinates\",LOCAL_DATUM[\"Object origin\",0],UNIT[\"metre\",1],AXIS[\"X\",EAST],AXIS[\"Y\",NORTH]]";
}
static SCPoint3 gis_point(SCPoint3 p,const SCMetrics *m,double s) {
    return (SCPoint3){(p.x-(m->min_x+m->max_x)/2)*s,
                     -(p.z-(m->min_z+m->max_z)/2)*s,(p.y-m->min_y)*s};
}
static void shape_header(Bytes *b,int type,size_t total,const double *bounds) {
    be32(b,9994); for(int i=0;i<5;i++) be32(b,0); be32(b,(uint32_t)(total/2));
    le32(b,1000); le32(b,(uint32_t)type);
    for(int i=0;i<6;i++) f64(b,type==5&&i>=4?0:bounds[i]);
    f64(b,type==5?0:-1e39); f64(b,type==5?0:-1e39);
}
/* Bounds: minX,minY,maxX,maxY,minZ,maxZ. */
static Bytes shape_record(int type,const SCPoint3 *p,int32_t np,const int32_t *parts,int32_t nr,const int32_t *kinds) {
    Bytes b=bytes(); double bounds[6]={p[0].x,p[0].y,p[0].x,p[0].y,p[0].z,p[0].z};
    for(int32_t i=1;i<np;i++) {
        bounds[0]=fmin(bounds[0],p[i].x); bounds[1]=fmin(bounds[1],p[i].y);
        bounds[2]=fmax(bounds[2],p[i].x); bounds[3]=fmax(bounds[3],p[i].y);
        bounds[4]=fmin(bounds[4],p[i].z); bounds[5]=fmax(bounds[5],p[i].z);
    }
    le32(&b,(uint32_t)type); for(int i=0;i<4;i++) f64(&b,bounds[i]);
    le32(&b,(uint32_t)nr); le32(&b,(uint32_t)np);
    for(int32_t i=0;i<nr;i++) le32(&b,(uint32_t)parts[i]);
    if(type==31) for(int32_t i=0;i<nr;i++) le32(&b,(uint32_t)kinds[i]);
    for(int32_t i=0;i<np;i++) { f64(&b,p[i].x); f64(&b,p[i].y); }
    if(type==13||type==15||type==31) {
        f64(&b,bounds[4]); f64(&b,bounds[5]); for(int32_t i=0;i<np;i++) f64(&b,p[i].z);
        f64(&b,-1e39); f64(&b,-1e39); for(int32_t i=0;i<np;i++) f64(&b,-1e39);
    }
    return b;
}
static int write_shapes(const char *dir,const char *base,int type,Bytes *records,int32_t nr,const double *bounds,char *e,size_t en) {
    size_t n=100;
    for(int32_t i=0;i<nr;i++) { if(!records[i].ok) return fail(e,en,"Not enough memory for shapefile."); n+=8+records[i].n; }
    if(n>INT32_MAX) return fail(e,en,"Shapefile exceeds size limit.");
    Bytes shp=bytes(),shx=bytes(); shape_header(&shp,type,n,bounds); shape_header(&shx,type,100+(size_t)nr*8,bounds);
    size_t offset=100;
    for(int32_t i=0;i<nr;i++) {
        be32(&shp,(uint32_t)i+1); be32(&shp,(uint32_t)(records[i].n/2)); put(&shp,records[i].d,records[i].n);
        be32(&shx,(uint32_t)(offset/2)); be32(&shx,(uint32_t)(records[i].n/2)); offset+=8+records[i].n;
    }
    char name[128]; snprintf(name,sizeof(name),"%s.shp",base); int ok=write_bytes(dir,name,&shp,e,en);
    if(ok) { snprintf(name,sizeof(name),"%s.shx",base); ok=write_bytes(dir,name,&shx,e,en); }
    free(shp.d); free(shx.d);
    if(ok) {
        Bytes prj=bytes(); put(&prj,sc_local_crs(),strlen(sc_local_crs())); snprintf(name,sizeof(name),"%s.prj",base);
        ok=write_bytes(dir,name,&prj,e,en); free(prj.d);
    }
    if(ok) { Bytes cpg=bytes(); put(&cpg,"UTF-8",5); snprintf(name,sizeof(name),"%s.cpg",base); ok=write_bytes(dir,name,&cpg,e,en); free(cpg.d); }
    return ok;
}
typedef struct { const char *name; char type; uint8_t width,decimals; } Field;
static Bytes dbf_start(const Field *f,int nf,int32_t rows) {
    Bytes b=bytes(); time_t now=time(NULL); struct tm *tm=gmtime(&now);
    int rowlen=1; for(int i=0;i<nf;i++) rowlen+=f[i].width;
    u8(&b,3); u8(&b,tm?(unsigned)(tm->tm_year):126); u8(&b,tm?(unsigned)(tm->tm_mon+1):1); u8(&b,tm?(unsigned)tm->tm_mday:1);
    le32(&b,(uint32_t)rows); le16(&b,(uint16_t)(32+32*nf+1)); le16(&b,(uint16_t)rowlen);
    for(int i=0;i<20;i++) u8(&b,0);
    for(int i=0;i<nf;i++) {
        for(int j=0;j<11;j++) u8(&b,j<(int)strlen(f[i].name)?(unsigned char)f[i].name[j]:0);
        u8(&b,(unsigned char)f[i].type); le32(&b,0); u8(&b,f[i].width); u8(&b,f[i].decimals);
        for(int j=0;j<14;j++) u8(&b,0);
    }
    u8(&b,13); return b;
}
static void dbf_text(Bytes *b,const char *s,unsigned width) {
    size_t n=strlen(s); for(unsigned i=0;i<width;i++) u8(b,i<n&&((unsigned char)s[i])<128?(unsigned char)s[i]:' ');
}
static void dbf_number(Bytes *b,double v,unsigned width,unsigned decimals) {
    char s[128]; int n=isfinite(v)?snprintf(s,sizeof(s),"%.*f",(int)decimals,v):0;
    if(n<0||n>(int)width) { b->ok=0; return; }
    for(unsigned i=0;i<width-(unsigned)n;i++) u8(b,' ');
    if(n) put(b,s,(size_t)n);
}
static int write_summary_dbf(const char *dir,const char *base,const SCMetrics *m,const char *source,char *e,size_t en) {
    Field f[]={{"WIDTH_M",'N',20,8},{"DEPTH_M",'N',20,8},{"HEIGHT_M",'N',20,8},
               {"AREA_M2",'N',20,8},{"VOLUME_M3",'N',20,10},{"VOL_VALID",'L',1,0},
               {"CLOSED",'L',1,0},{"COMPONENTS",'N',10,0},{"SCALE_SRC",'C',12,0},{"UNIT",'C',1,0}};
    Bytes b=dbf_start(f,10,1); u8(&b,' ');
    dbf_number(&b,m->width_m,20,8); dbf_number(&b,m->depth_m,20,8); dbf_number(&b,m->height_m,20,8);
    dbf_number(&b,strcmp(base,"footprint")==0?m->footprint_m2:m->surface_m2,20,8);
    dbf_number(&b,m->volume_m3,20,10); u8(&b,m->volume_valid?'T':'F'); u8(&b,m->watertight?'T':'F');
    dbf_number(&b,m->components,10,0); dbf_text(&b,source,12); u8(&b,'m'); u8(&b,26);
    char name[128]; snprintf(name,sizeof(name),"%s.dbf",base); int ok=write_bytes(dir,name,&b,e,en); free(b.d); return ok;
}

int sc_export_bundle(const char *dir,const SCPoint3 *v,int32_t nv,const SCTriangle *tri,int32_t nt,
                     const SCLine *lines,int32_t nl,double scale,const char *source,const char *name,char *e,size_t en) {
    SCMetrics m;
    if(!dir||!source||!name||nl<0||nl>SC_MAX_LINES||(nl&&!lines)) return fail(e,en,"Invalid export arguments.");
    if(strcmp(source,"LIDAR")&&strcmp(source,"REFERENCE")&&strcmp(source,"DEMO")) return fail(e,en,"Verify scale before metric export.");
    if(!sc_analyze(v,nv,tri,nt,scale,&m,e,en)) return 0;
    for(int32_t i=0;i<nl;i++) if(!finite_point(lines[i].a)||!finite_point(lines[i].b)||
        fabs(lines[i].a.x)>1e9||fabs(lines[i].a.y)>1e9||fabs(lines[i].a.z)>1e9||
        fabs(lines[i].b.x)>1e9||fabs(lines[i].b.y)>1e9||fabs(lines[i].b.z)>1e9||
        !memchr(lines[i].label,0,sizeof(lines[i].label))) return fail(e,en,"Invalid saved measurement.");
    double bounds[6]={-m.width_m/2,-m.depth_m/2,m.width_m/2,m.depth_m/2,0,m.height_m};
    SCPoint3 *mp=malloc((size_t)nt*3*sizeof(*mp)); int32_t *parts=malloc((size_t)nt*sizeof(*parts));
    int32_t *kinds=malloc((size_t)nt*sizeof(*kinds));
    if(!mp||!parts||!kinds) { free(mp); free(parts); free(kinds); return fail(e,en,"Not enough memory to export mesh."); }
    for(int32_t i=0;i<nt;i++) {
        mp[3*i]=gis_point(v[tri[i].a],&m,scale); mp[3*i+1]=gis_point(v[tri[i].b],&m,scale); mp[3*i+2]=gis_point(v[tri[i].c],&m,scale);
        parts[i]=3*i; kinds[i]=1; /* Each triangle is a triangle fan MultiPatch part. */
    }
    Bytes mesh=shape_record(31,mp,nt*3,parts,nt,kinds); free(mp); free(parts); free(kinds);
    int ok=write_shapes(dir,"mesh",31,&mesh,1,bounds,e,en); free(mesh.d);
    if(ok) ok=write_summary_dbf(dir,"mesh",&m,source,e,en);
    int32_t hn=0; XY *h=hull(v,nv,&hn);
    if(!h) return fail(e,en,"Not enough memory to export footprint.");
    /* A purely vertical model has no nondegenerate XY footprint. Reject instead of inventing a polygon. */
    if(hn<3||m.footprint_m2<=0) { free(h); return fail(e,en,"Object has no two-dimensional footprint. Scan all sides."); }
    SCPoint3 *hp=malloc((size_t)(hn+1)*sizeof(*hp));
    if(!hp) { free(h); return fail(e,en,"Not enough memory to export footprint."); }
    for(int32_t i=0;i<hn;i++) {
        XY q=h[hn-1-i]; /* Shapefile outer ring must be clockwise. */
        hp[i]=(SCPoint3){(q.x-(m.min_x+m.max_x)/2)*scale,(q.y+(m.min_z+m.max_z)/2)*scale,0};
    }
    hp[hn]=hp[0]; int32_t first=0; Bytes fp=shape_record(5,hp,hn+1,&first,1,NULL); free(hp); free(h);
    if(ok) ok=write_shapes(dir,"footprint",5,&fp,1,bounds,e,en);
    free(fp.d);
    if(ok) ok=write_summary_dbf(dir,"footprint",&m,source,e,en);

    /* Three axis-aligned dimensions followed by any user-selected point-to-point measurements. */
    int32_t count=nl+3; Bytes *lr=calloc((size_t)count,sizeof(*lr));
    SCLine *all=calloc((size_t)count,sizeof(*all));
    if(!lr||!all) { free(lr); free(all); return fail(e,en,"Not enough memory for measurement layer."); }
    SCPoint3 origin={m.min_x,m.min_y,m.min_z};
    all[0]=(SCLine){origin,{m.max_x,m.min_y,m.min_z},"Width (axis aligned)"};
    all[1]=(SCLine){origin,{m.min_x,m.min_y,m.max_z},"Depth (axis aligned)"};
    all[2]=(SCLine){origin,{m.min_x,m.max_y,m.min_z},"Height (axis aligned)"};
    if(nl) memcpy(all+3,lines,(size_t)nl*sizeof(*all));
    Field lf[]={{"ID",'N',8,0},{"LABEL",'C',47,0},{"LENGTH_M",'N',20,8},{"KIND",'C',8,0},{"SCALE_SRC",'C',12,0}};
    Bytes dbf=dbf_start(lf,5,count),csv=bytes();
    textf(&csv,"id,label,length_m,kind,scale_source\n");
    double lb[6]={INFINITY,INFINITY,-INFINITY,-INFINITY,INFINITY,-INFINITY};
    for(int32_t i=0;i<count;i++) {
        SCPoint3 p[2]={gis_point(all[i].a,&m,scale),gis_point(all[i].b,&m,scale)};
        for(int j=0;j<2;j++) { lb[0]=fmin(lb[0],p[j].x); lb[1]=fmin(lb[1],p[j].y); lb[2]=fmax(lb[2],p[j].x); lb[3]=fmax(lb[3],p[j].y); lb[4]=fmin(lb[4],p[j].z); lb[5]=fmax(lb[5],p[j].z); }
        lr[i]=shape_record(13,p,2,&first,1,NULL); double length=norm(sub(all[i].b,all[i].a))*scale;
        u8(&dbf,' '); dbf_number(&dbf,i+1,8,0); dbf_text(&dbf,all[i].label,47); dbf_number(&dbf,length,20,8);
        dbf_text(&dbf,i<3?"BOUNDING":"CUSTOM",8); dbf_text(&dbf,source,12);
        textf(&csv,"%d,",i+1); csv_string(&csv,all[i].label); textf(&csv,",%.12g,%s,%s\n",length,i<3?"BOUNDING":"CUSTOM",source);
    }
    u8(&dbf,26);
    if(ok) ok=write_shapes(dir,"measures",13,lr,count,lb,e,en);
    if(ok) ok=write_bytes(dir,"measures.dbf",&dbf,e,en);
    if(ok) ok=write_bytes(dir,"measurements.csv",&csv,e,en);
    for(int32_t i=0;i<count;i++) free(lr[i].d);
    free(lr); free(dbf.d); free(csv.d);

    Bytes obj=bytes(); textf(&obj,"# ShapeMeasure; local X,Y,Z in metres; Z up; scale_source=%s\n",source);
    for(int32_t i=0;i<nv;i++) { SCPoint3 p=gis_point(v[i],&m,scale); textf(&obj,"v %.12g %.12g %.12g\n",p.x,p.y,p.z); }
    for(int32_t i=0;i<nt;i++) textf(&obj,"f %d %d %d\n",tri[i].a+1,tri[i].b+1,tri[i].c+1);
    if(ok) ok=write_bytes(dir,"model.obj",&obj,e,en);
    free(obj.d);

    Bytes report=bytes(); textf(&report,"{\n  \"schema_version\": 1,\n  \"object_name\": "); json_string(&report,name);
    textf(&report,",\n  \"units\": \"metres\",\n  \"scale_source\": "); json_string(&report,source);
    textf(&report,",\n  \"scale_factor\": %.15g,\n  \"coordinate_system\": \"local engineering; not georeferenced\",\n",scale);
    textf(&report,"  \"axis_mapping\": \"GIS X=(model.x-center.x)*scale; Y=-(model.z-center.z)*scale; Z=(model.y-min.y)*scale\",\n");
    textf(&report,"  \"model_center_x\": %.15g, \"model_center_z\": %.15g, \"model_bottom_y\": %.15g,\n",(m.min_x+m.max_x)/2,(m.min_z+m.max_z)/2,m.min_y);
    textf(&report,"  \"bounding_box_m\": {\"width_x\": %.15g, \"depth_y\": %.15g, \"height_z\": %.15g},\n",m.width_m,m.depth_m,m.height_m);
    textf(&report,"  \"surface_area_m2\": %.15g,\n  \"convex_footprint_area_m2\": %.15g,\n  \"convex_footprint_perimeter_m\": %.15g,\n  \"volume_m3\": ",m.surface_m2,m.footprint_m2,m.footprint_perimeter_m);
    if(m.volume_valid) textf(&report,"%.15g",m.volume_m3); else textf(&report,"null");
    textf(&report,",\n  \"volume_valid\": %s, \"watertight\": %s,\n",m.volume_valid?"true":"false",m.watertight?"true":"false");
    textf(&report,"  \"topology\": {\"boundary_edges\": %d, \"nonmanifold_edges\": %d, \"winding_errors\": %d, \"components\": %d},\n",m.boundary_edges,m.nonmanifold_edges,m.winding_errors,m.components);
    textf(&report,"  \"vertex_count\": %d, \"triangle_count\": %d,\n  \"measurements\": [\n",nv,nt);
    for(int32_t i=0;i<count;i++) {
        SCPoint3 a=gis_point(all[i].a,&m,scale),b=gis_point(all[i].b,&m,scale);
        textf(&report,"    {\"label\": "); json_string(&report,all[i].label);
        textf(&report,", \"length_m\": %.15g, \"a\": [%.15g, %.15g, %.15g], \"b\": [%.15g, %.15g, %.15g]}%s\n",norm(sub(all[i].b,all[i].a))*scale,a.x,a.y,a.z,b.x,b.y,b.z,i+1<count?",":"");
    }
    textf(&report,"  ],\n  \"interpretation\": {\"bounds\": \"axis aligned, not minimum oriented dimensions\", \"footprint\": \"convex XY hull, includes concave voids\", \"surface\": \"observed reconstructed triangles\", \"volume\": \"only for one closed, consistently oriented component; self-intersections not tested\", \"precision\": \"decimal places are storage precision, not measurement accuracy\"}\n}\n");
    if(ok) ok=write_bytes(dir,"report.json",&report,e,en);
    free(report.d); free(all);

    Bytes readme=bytes(); const char *help=
        "ShapeMeasure export\n\n"
        "Keep each .shp/.shx/.dbf/.prj/.cpg set together. All coordinates use local metres; Z is up.\n"
        "These files are not positioned on Earth. Do not assign EPSG:4326 or another geographic CRS.\n\n"
        "footprint.shp: 2D clockwise convex hull of the object. Open this layer first in QGIS.\n"
        "mesh.shp: true 3D MultiPatch triangle fans, including vertical faces. View in a compatible\n"
        "3D GIS viewer, such as an ArcGIS Pro local scene. Reader support for MultiPatch varies.\n"
        "measures.shp: PolyLineZ bounds and saved straight point-to-point measurements.\n"
        "model.obj: calibrated geometry in the same local Z-up metric frame.\n"
        "report.json: full metrics, scale provenance, topology and interpretation.\n"
        "measurements.csv: lengths in metres, not distances along the surface.\n\n"
        "Object bounding dimensions follow the reconstructed model axes. They are not an oriented\n"
        "minimum bounding box. Footprint area includes concave voids. Surface area measures the\n"
        "reconstructed mesh and may include artifacts. Volume is blank/null unless a single closed\n"
        "consistently oriented component is found; self-intersection is not checked. Validate all\n"
        "important measurements against a ruler. LiDAR-assisted scale is an estimate. REFERENCE\n"
        "means the user set scale using a known length. DEMO means synthetic sample geometry.\n";
    put(&readme,help,strlen(help)); if(ok) ok=write_bytes(dir,"READ_ME.txt",&readme,e,en); free(readme.d);
    return ok;
}

static const char *bundle_files[]={
    "mesh.shp","mesh.shx","mesh.dbf","mesh.prj","mesh.cpg",
    "footprint.shp","footprint.shx","footprint.dbf","footprint.prj","footprint.cpg",
    "measures.shp","measures.shx","measures.dbf","measures.prj","measures.cpg",
    "model.obj","report.json","measurements.csv","READ_ME.txt"
};
static uint32_t crc_update(uint32_t crc,const unsigned char *p,size_t n) {
    for(size_t i=0;i<n;i++) { crc^=p[i]; for(int j=0;j<8;j++) crc=(crc>>1)^(0xedb88320u&(0u-(crc&1))); }
    return crc;
}
static int emit(FILE *f,Bytes *b) { return b->ok&&fwrite(b->d,1,b->n,f)==b->n; }
int sc_zip_bundle(const char *dir,const char *path,char *e,size_t en) {
    if(e&&en) e[0]=0;
    if(!dir||!path) return fail(e,en,"Missing archive path.");
    FILE *out=fopen(path,"wb"); if(!out) return fail(e,en,"Cannot create archive.");
    Bytes central=bytes(); int ok=1;
    for(size_t i=0;ok&&i<sizeof(bundle_files)/sizeof(bundle_files[0]);i++) {
        char input[4096];
        if(snprintf(input,sizeof(input),"%s/%s",dir,bundle_files[i])>=(int)sizeof(input)) { ok=fail(e,en,"Archive path too long."); break; }
        FILE *in=fopen(input,"rb"); if(!in) { ok=fail(e,en,"Missing component %s.",bundle_files[i]); break; }
        uint32_t crc=0xffffffffu; uint64_t length=0; unsigned char buffer[65536]; size_t n;
        while((n=fread(buffer,1,sizeof(buffer),in))) { crc=crc_update(crc,buffer,n); length+=n; }
        if(ferror(in)||length>UINT32_MAX) { fclose(in); ok=fail(e,en,"Archive component cannot be read or is too large."); break; }
        rewind(in); crc^=0xffffffffu; long offset=ftell(out);
        if(offset<0||(uint64_t)offset>UINT32_MAX) { fclose(in); ok=fail(e,en,"Archive size limit exceeded."); break; }
        uint16_t namelen=(uint16_t)strlen(bundle_files[i]); Bytes head=bytes();
        le32(&head,0x04034b50); le16(&head,20); le16(&head,0); le16(&head,0); le16(&head,0); le16(&head,33);
        le32(&head,crc); le32(&head,(uint32_t)length); le32(&head,(uint32_t)length); le16(&head,namelen); le16(&head,0);
        put(&head,bundle_files[i],namelen); ok=emit(out,&head); free(head.d);
        while(ok&&(n=fread(buffer,1,sizeof(buffer),in))) if(fwrite(buffer,1,n,out)!=n) ok=0;
        if(ferror(in)) ok=0;
        fclose(in);
        le32(&central,0x02014b50); le16(&central,20); le16(&central,20); le16(&central,0); le16(&central,0);
        le16(&central,0); le16(&central,33); le32(&central,crc); le32(&central,(uint32_t)length); le32(&central,(uint32_t)length);
        le16(&central,namelen); le16(&central,0); le16(&central,0); le16(&central,0); le16(&central,0); le32(&central,0);
        le32(&central,(uint32_t)offset); put(&central,bundle_files[i],namelen);
    }
    if(ok) {
        long at=ftell(out); Bytes end=bytes();
        if(at<0||(uint64_t)at+central.n>UINT32_MAX) ok=0;
        if(ok) {
            uint16_t count=(uint16_t)(sizeof(bundle_files)/sizeof(bundle_files[0]));
            le32(&end,0x06054b50); le16(&end,0); le16(&end,0); le16(&end,count); le16(&end,count);
            le32(&end,(uint32_t)central.n); le32(&end,(uint32_t)at); le16(&end,0);
            ok=emit(out,&central)&&emit(out,&end);
        }
        free(end.d);
    }
    free(central.d); if(fclose(out)!=0) ok=0;
    if(!ok) { remove(path); if(!e||!en||!e[0]) fail(e,en,"Could not finish archive."); }
    return ok;
}
